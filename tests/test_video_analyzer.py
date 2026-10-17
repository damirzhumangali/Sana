from types import SimpleNamespace

import cv2
import numpy as np
import pytest

from app.services import video_analyzer as video

CASHIER = [160, 400, 260, 600]
CLIENT = [170, 260, 250, 420]
ENTERING = [75, 180, 145, 360]
REFLECTION = [1.1, 221.7, 56.3, 291.6]


def detection(coords, class_id=0, confidence=0.9):
    return SimpleNamespace(cls=[class_id], conf=[confidence], xyxy=np.array([coords], dtype=float))


class Model:
    names = {0: "person", 1: "laptop", 2: "id_card"}

    def __init__(self, boxes=None, sequence=None):
        self.boxes = [] if boxes is None else boxes
        self.sequence = iter(sequence) if sequence is not None else None
        self.calls = 0

    def __call__(self, frame, verbose=False):
        self.calls += 1
        boxes = next(self.sequence) if self.sequence is not None else self.boxes
        return [SimpleNamespace(boxes=boxes)]


class Capture:
    def __init__(self, frame_count=20, fps=2.0, opened=True, reported_count=None, timestamps=None):
        self.frame_count = frame_count
        self.fps = fps
        self.opened = opened
        self.reported_count = frame_count if reported_count is None else reported_count
        self.index = 0
        self.released = False
        self.timestamps = timestamps

    def isOpened(self):
        return self.opened

    def get(self, prop):
        if prop == cv2.CAP_PROP_FPS:
            return self.fps
        if prop == cv2.CAP_PROP_POS_MSEC:
            if self.timestamps is not None:
                return self.timestamps[self.index - 1] * 1000
            return 0
        return self.reported_count

    def read(self):
        if self.index >= self.frame_count:
            return False, None
        frame = np.full((848, 464, 3), self.index, dtype=np.uint8)
        # Large white desk patch is deliberately not interpreted as verified ID.
        frame[410:435, 140:190] = 255
        self.index += 1
        return True, frame

    def release(self):
        self.released = True


@pytest.fixture
def video_path(tmp_path):
    path = tmp_path / "input.mov"
    path.write_bytes(b"fake container, capture is injected")
    return path


def use_capture(monkeypatch, **kwargs):
    cap = Capture(**kwargs)
    monkeypatch.setattr(video.cv2, "VideoCapture", lambda path: cap)
    return cap


def test_lone_cashier_and_reflection_are_clean(monkeypatch, video_path):
    cap = use_capture(monkeypatch)
    model = Model([detection(CASHIER), detection(REFLECTION), detection(CLIENT, class_id=1)])
    result = video.VideoAnalyzer(model=model).analyze(video_path)
    assert result.verdict == "no_violation"
    assert model.calls == 20
    assert cap.released


def test_waiting_client_does_not_prove_missing_id(monkeypatch, video_path):
    use_capture(monkeypatch, frame_count=40)
    model = Model([detection(CASHIER), detection(CLIENT)])
    result = video.VideoAnalyzer(model=model).analyze(video_path)
    assert result.violations == []


def test_generic_laptop_is_not_an_open_screen(monkeypatch, video_path):
    use_capture(monkeypatch)
    model = Model(sequence=[[detection(CASHIER)]] + [[detection(CLIENT, class_id=1)]] * 19)
    result = video.VideoAnalyzer(model=model).analyze(video_path)
    assert result.verdict == "no_violation"


def test_confirmed_unattended_laptop_has_current_frame_evidence(monkeypatch, video_path):
    use_capture(monkeypatch)
    model = Model(sequence=[[detection(CASHIER)]] + [[]] * 19)
    result = video.VideoAnalyzer(
        model=model, signal_provider=lambda frame, timestamp: {"laptop_open": True}
    ).analyze(video_path)
    assert result.verdict == "violation"
    assert len(result.violations) == 1
    violation = result.violations[0]
    assert violation.timecode_sec > 4.5
    assert violation.evidence_frames[0].timestamp_sec == violation.timecode_sec
    # Capture is not reopened to extract evidence; it contains the analyzed frame.
    import base64

    decoded = cv2.imdecode(
        np.frombuffer(base64.b64decode(violation.evidence_frames[0].image_base64), np.uint8),
        cv2.IMREAD_COLOR,
    )
    assert abs(int(decoded[0, 0, 0]) - int(violation.timecode_sec * 2)) <= 1


def test_two_people_in_doorway_use_continuous_elapsed_time(monkeypatch, video_path):
    use_capture(monkeypatch, frame_count=6)
    model = Model([detection(CASHIER), detection(ENTERING), detection([85, 170, 155, 350])])
    result = video.VideoAnalyzer(model=model).analyze(video_path)
    assert len(result.violations) == 1
    assert result.violations[0].timecode_sec == 2.0


def test_sampling_uses_video_timestamps(monkeypatch, video_path):
    use_capture(monkeypatch, frame_count=30, fps=10)
    samples = []

    def signals(frame, timestamp):
        samples.append(timestamp)
        return {}

    video.VideoAnalyzer(sample_fps=3, model=Model(), signal_provider=signals).analyze(video_path)
    assert samples == pytest.approx([0, 0.4, 0.7, 1, 1.4, 1.7, 2, 2.4, 2.7])


def test_variable_frame_rate_uses_decoder_clock(monkeypatch, video_path):
    timestamps = [0, 0.2, 0.55, 0.65, 1.1, 1.65]
    use_capture(monkeypatch, frame_count=6, fps=10, timestamps=timestamps)
    samples = []

    def signals(frame, timestamp):
        samples.append(timestamp)
        return {}

    video.VideoAnalyzer(model=Model(), signal_provider=signals).analyze(video_path)
    assert samples == pytest.approx([0, 0.55, 1.1, 1.65])


@pytest.mark.parametrize("fps", [0, -1, float("inf"), float("nan")])
def test_reject_invalid_video_timing(monkeypatch, video_path, fps):
    cap = use_capture(monkeypatch, fps=fps)
    with pytest.raises(ValueError, match="frame rate"):
        video.VideoAnalyzer(model=Model()).analyze(video_path)
    assert cap.released


@pytest.mark.parametrize("sample_fps", [0, 0.25, -1, float("inf"), float("nan")])
def test_reject_invalid_sampling_rate(sample_fps):
    with pytest.raises(ValueError, match="sample_fps"):
        video.VideoAnalyzer(sample_fps=sample_fps, model=Model())


@pytest.mark.parametrize(
    ("capture_kwargs", "message"),
    [
        ({"opened": False}, "could not open"),
        ({"frame_count": 0}, "no decodable"),
        ({"frame_count": 2, "reported_count": 10}, "advertised end"),
    ],
)
def test_decode_failures_are_errors(monkeypatch, video_path, capture_kwargs, message):
    cap = use_capture(monkeypatch, **capture_kwargs)
    with pytest.raises(RuntimeError, match=message):
        video.VideoAnalyzer(model=Model()).analyze(video_path)
    assert cap.released


def test_release_capture_on_model_failure(monkeypatch, video_path):
    cap = use_capture(monkeypatch)

    def broken_model(frame, **kwargs):
        raise RuntimeError("inference failed")

    with pytest.raises(RuntimeError, match="inference failed"):
        video.VideoAnalyzer(model=broken_model).analyze(video_path)
    assert cap.released


@pytest.mark.parametrize("signals", [{"cashier_present": False}, {"laptop_open": 1}, []])
def test_reject_invalid_signal_provider(monkeypatch, video_path, signals):
    cap = use_capture(monkeypatch)
    with pytest.raises(ValueError, match="signal|mapping"):
        video.VideoAnalyzer(
            model=Model(), signal_provider=lambda frame, timestamp: signals
        ).analyze(video_path)
    assert cap.released


def test_missing_weights_never_attempt_model_loading(monkeypatch, tmp_path):
    def unexpected_load(path):
        pytest.fail("Missing weights must not trigger model loading or downloading")

    monkeypatch.setattr(video, "_load_local_model", unexpected_load)
    with pytest.raises(FileNotFoundError, match="local YOLO weights"):
        video.get_yolo_model(tmp_path / "missing.pt")


def test_architecture_config_is_not_trained_weights(monkeypatch, tmp_path):
    path = tmp_path / "model.yaml"
    path.write_text("nc: 80", encoding="utf-8")

    def unexpected_load(path):
        pytest.fail("An architecture config must not instantiate untrained YOLO")

    monkeypatch.setattr(video, "_load_local_model", unexpected_load)
    with pytest.raises(ValueError, match="trained local .pt"):
        video.get_yolo_model(path)


def test_video_settings_use_environment(monkeypatch, tmp_path):
    monkeypatch.setenv("VIDEO_MODEL_PATH", str(tmp_path / "model.pt"))
    monkeypatch.setenv("VIDEO_SAMPLE_FPS", "3")
    monkeypatch.setenv("VIDEO_SIGNAL_PROVIDER", "package.signals:observe_frame")
    settings = video.VideoAnalysisSettings(_env_file=None)
    assert settings.video_model_path == tmp_path / "model.pt"
    assert settings.video_sample_fps == 3
    assert settings.video_signal_provider == "package.signals:observe_frame"


def test_real_opencv_decode_with_injected_model(tmp_path):
    path = tmp_path / "synthetic.avi"
    writer = cv2.VideoWriter(str(path), cv2.VideoWriter_fourcc(*"MJPG"), 5, (464, 848))
    assert writer.isOpened()
    try:
        for index in range(15):
            writer.write(np.full((848, 464, 3), index, dtype=np.uint8))
    finally:
        writer.release()
    model = Model([detection(CASHIER)])
    result = video.VideoAnalyzer(model=model).analyze(path)
    assert result.verdict == "no_violation"
    assert model.calls == 6


def test_legacy_entry_point_delegates_to_real_analyzer(monkeypatch, video_path):
    from app.services.analyzer import analyze_video

    result = {"verdict": "no_violation", "violations": []}

    def local_analyzer(path):
        assert path == video_path
        return result

    monkeypatch.setattr(video, "analyze_video", local_analyzer)
    assert analyze_video(video_path) == result
