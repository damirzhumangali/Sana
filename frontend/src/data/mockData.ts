import type { JobDone, JobHistoryItem } from '../types/api'

// Helper to create an SVG data URI frame with overlay bounding boxes
function createMockFrame(timecode: string, ruleText: string, alertType: 'warning' | 'danger' | 'success', details: string): string {
  const color = alertType === 'danger' ? '#ef4444' : alertType === 'warning' ? '#f59e0b' : '#10b981'
  const svg = `<svg xmlns="http://www.w3.org/2000/svg" width="640" height="360" viewBox="0 0 640 360">
    <rect width="640" height="360" fill="#0f172a"/>
    <!-- Grid overlay -->
    <defs>
      <pattern id="grid" width="40" height="40" patternUnits="userSpaceOnUse">
        <path d="M 40 0 L 0 0 0 40" fill="none" stroke="#1e293b" stroke-width="0.5"/>
      </pattern>
    </defs>
    <rect width="640" height="360" fill="url(#grid)"/>

    <!-- Simulated camera perspective -->
    <polygon points="120,60 520,60 600,340 40,340" fill="#1e293b" opacity="0.3"/>

    <!-- Cashier Counter Zone -->
    <polygon points="140,160 500,160 560,330 80,330" fill="#0284c7" opacity="0.1" stroke="#0284c7" stroke-dasharray="4 4" stroke-width="1.5"/>
    <text x="90" y="320" fill="#38bdf8" font-family="monospace" font-size="11" font-weight="bold">ZONE: CASH_REGISTER_INTERNAL</text>

    <!-- Cashier BBox -->
    <rect x="230" y="80" width="130" height="190" fill="none" stroke="#06b6d4" stroke-width="2"/>
    <rect x="230" y="62" width="130" height="18" fill="#06b6d4"/>
    <text x="235" y="75" fill="#090d16" font-family="sans-serif" font-size="10" font-weight="bold">PERSON: CASHIER [0.96]</text>

    <!-- Violation ROI Box -->
    <rect x="360" y="150" width="160" height="140" fill="${color}" fill-opacity="0.15" stroke="${color}" stroke-width="2.5"/>
    <rect x="360" y="130" width="160" height="20" fill="${color}"/>
    <text x="365" y="144" fill="#ffffff" font-family="sans-serif" font-size="10" font-weight="bold">AI DETECT: ${ruleText}</text>

    <!-- Camera HUD Header -->
    <rect x="10" y="10" width="620" height="30" rx="4" fill="#090d16" fill-opacity="0.85"/>
    <circle cx="25" cy="25" r="5" fill="${color}"/>
    <text x="38" y="29" fill="#f8fafc" font-family="monospace" font-size="12" font-weight="bold">CAM_04 // KASSA_ZONE_HQ</text>
    <text x="240" y="29" fill="#94a3b8" font-family="monospace" font-size="12">TIMECODE: ${timecode}</text>
    <text x="460" y="29" fill="#38bdf8" font-family="monospace" font-size="12">FPS: 25.0 // 1080p</text>

    <!-- Details footer banner -->
    <rect x="20" y="300" width="600" height="42" rx="6" fill="#090d16" fill-opacity="0.9" stroke="#334155" stroke-width="1"/>
    <text x="35" y="325" fill="#e2e8f0" font-family="sans-serif" font-size="12">${details}</text>
  </svg>`

  return `data:image/svg+xml;utf8,${encodeURIComponent(svg)}`
}

export const MOCK_SCENARIOS: { id: string; title: string; description: string; data: JobDone }[] = [
  {
    id: 'scenario-receipt-miss',
    title: 'Кейс 1: Невыдача фискального чека + Открытый ящик',
    description: 'Два зафиксированных инцидента: отсутствие фискализации при наличном расчете и оставленная касса без присмотра.',
    data: {
      job_id: 'job_mock_receipt_violation_01',
      status: 'done',
      verdict: 'violation',
      violations: [
        {
          type: 'Невыдача фискального чека при денежном расчете',
          timecode_sec: 14.5,
          description: 'Клиент передает наличные купюры (номинал 2000 ₸). Кассир принимает оплату, убирает в лоток, передает пакет с товаром. ККМ-терминал не активирован, чек не напечатан и не передан покупателю.',
          rule_id: 'R-01',
          source: 'Кодекс Республики Казахстан «О налогах и других обязательных платежах в бюджет» (Налоговый кодекс)',
          clause: 'ст. 166, п. 1',
          confidence: 0.94,
          evidence_frames: [
            {
              timestamp_sec: 14.5,
              image_base64: createMockFrame('00:00:14.500', 'NO_RECEIPT_ISSUED', 'danger', 'Инцидент #1: Факт передачи товара без печати фискального чека')
            },
            {
              timestamp_sec: 16.2,
              image_base64: createMockFrame('00:00:16.200', 'CLIENT_LEAVES', 'danger', 'Покупатель покинул кассовую зону без чека')
            }
          ]
        },
        {
          type: 'Оставление кассовой зоны без присмотра при открытом денежном ящике',
          timecode_sec: 38.0,
          description: 'Кассир встает и выходит из кассовой кабины. Выдвижной лоток с денежными средствами остался в открытом положении без физического надзора более 15 секунд.',
          rule_id: 'R-03',
          source: 'Типовые правила эксплуатации контрольно-кассовых узлов и кассовой дисциплины',
          clause: 'п. 12',
          confidence: 0.89,
          evidence_frames: [
            {
              timestamp_sec: 38.0,
              image_base64: createMockFrame('00:00:38.000', 'DRAWER_UNATTENDED', 'warning', 'Инцидент #2: Кассир вне зоны, лоток с наличными открыт')
            }
          ]
        }
      ]
    }
  },
  {
    id: 'scenario-intruder',
    title: 'Кейс 2: Постороннее лицо в служебной зоне кассы',
    description: 'Зафиксировано проникновение гражданского лица без униформы за линию прилавка в зону хранения наличных.',
    data: {
      job_id: 'job_mock_intruder_02',
      status: 'done',
      verdict: 'violation',
      violations: [
        {
          type: 'Нахождение постороннего лица в служебной кассовой зоне',
          timecode_sec: 22.4,
          description: 'Лицо в гражданской одежде (без бейджа и корпоративной униформы) зашло за защитную стойку в рабочую зону за спиной кассира.',
          rule_id: 'R-02',
          source: 'Правила внутренней безопасности и кассовой дисциплины торговых объектов',
          clause: 'п. 4.2',
          confidence: 0.96,
          evidence_frames: [
            {
              timestamp_sec: 22.4,
              image_base64: createMockFrame('00:00:22.400', 'UNAUTHORIZED_ACCESS', 'danger', 'Инцидент: Посторонний гражданин в зоне хранения денежных средств')
            }
          ]
        }
      ]
    }
  },
  {
    id: 'scenario-clean',
    title: 'Кейс 3: Нарушений не выявлено (Чистая смена)',
    description: 'Все финансовые операции сопровождались фискализацией через ККМ. Посторонние лица отсутствуют.',
    data: {
      job_id: 'job_mock_clean_session_03',
      status: 'done',
      verdict: 'no_violation',
      violations: []
    }
  }
]

export const INITIAL_HISTORY: JobHistoryItem[] = [
  {
    id: 'hist-1',
    jobId: 'job_mock_receipt_violation_01',
    createdAt: Date.now() - 1000 * 60 * 18,
    fileName: 'kassa_cam4_shift1_14-10-2026.mp4',
    fileSizeBytes: 42 * 1024 * 1024,
    status: 'done',
    verdict: 'violation',
    violationsCount: 2,
    jobData: MOCK_SCENARIOS[0].data
  },
  {
    id: 'hist-2',
    jobId: 'job_mock_intruder_02',
    createdAt: Date.now() - 1000 * 60 * 45,
    fileName: 'kassa_cam2_night_access.mp4',
    fileSizeBytes: 18 * 1024 * 1024,
    status: 'done',
    verdict: 'violation',
    violationsCount: 1,
    jobData: MOCK_SCENARIOS[1].data
  },
  {
    id: 'hist-3',
    jobId: 'job_mock_clean_session_03',
    createdAt: Date.now() - 1000 * 60 * 120,
    fileName: 'kassa_cam1_morning_rush.mp4',
    fileSizeBytes: 84 * 1024 * 1024,
    status: 'done',
    verdict: 'no_violation',
    violationsCount: 0,
    jobData: MOCK_SCENARIOS[2].data
  }
]
