"""One-file multipart parsing with deterministic cleanup, including truncated bodies."""

from contextlib import aclosing
from pathlib import Path

from starlette.datastructures import FormData
from starlette.exceptions import HTTPException
from starlette.formparsers import MultiPartException, MultiPartParser, parse_options_header
from starlette.requests import Request


class VideoMultipartParser(MultiPartParser):
    def __init__(self, headers, stream, max_file_bytes: int) -> None:
        super().__init__(headers, stream, max_files=1, max_fields=0)
        self.max_file_bytes = max_file_bytes
        self.file_bytes = 0
        self.ended = False

    def on_headers_finished(self) -> None:
        super().on_headers_finished()
        part = self._current_part
        if part.field_name != "file":
            raise MultiPartException("Expected one file in the 'file' field")
        if part.file is None or Path(part.file.filename or "").suffix.lower() != ".mov":
            raise MultiPartException("Only .mov files are supported")

    def on_part_data(self, data: bytes, start: int, end: int) -> None:
        self.file_bytes += end - start
        if self.file_bytes > self.max_file_bytes:
            raise HTTPException(
                413,
                detail={
                    "code": "FILE_TOO_LARGE",
                    "message": "File exceeds the configured upload limit",
                },
            )
        super().on_part_data(data, start, end)

    def on_end(self) -> None:
        self.ended = True
        super().on_end()

    async def parse(self) -> FormData:
        try:
            form = await super().parse()
            # python-multipart.finalize() currently does not reject truncated bodies.
            if not self.ended:
                raise MultiPartException("Incomplete multipart body")
            return form
        except BaseException:
            # Include a file whose part never finished and is absent from FormData.items.
            for temporary_file in self._files_to_close_on_error:
                temporary_file.close()
            raise


async def parse_video_form(request: Request, max_file_bytes: int) -> FormData:
    content_type, _ = parse_options_header(request.headers.get("content-type"))
    if content_type != b"multipart/form-data":
        return FormData()
    async with aclosing(request.stream()) as stream:
        parser = VideoMultipartParser(request.headers, stream, max_file_bytes)
        try:
            return await parser.parse()
        except MultiPartException as exc:
            raise HTTPException(status_code=400, detail=exc.message) from exc
