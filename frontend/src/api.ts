// Typed client for the Fiction Companion backend (see the repo README, "Endpoints").

export const API_URL: string = (import.meta.env.VITE_API_URL as string | undefined) ?? 'http://localhost:8000'

export type BookStatus = 'pending' | 'indexing' | 'ready' | 'failed'

export interface Book {
  id: number
  title: string
  author: string | null // from the EPUB; older titles may carry it as "Title--Author" instead
  status: BookStatus
  indexed_through: number
  sentences: number
  version: string
}

export interface Passage {
  id: number
  paragraph_pos: number
  href: string
  para_no: number
  first_sentence: number
  sentence_ends: number[]
  text: string
}

export interface Character {
  id: number
  name: string
  names: string[]
  first_seen: number
  lines: number
  last_heard: number | null // sentence of their latest line before the bookmark
  facts: number
  has_audio_voice?: boolean
  has_voice_profile: boolean
}

export interface Turn {
  role: 'reader' | 'character'
  text: string
  evidence_ids?: number[]
}

export interface CitationSource {
  passage_id: number
  paragraph_pos: number
  text: string
  truncated: boolean
  lo: number
  hi: number
  quote: string
  role?: string
}

export interface Citation {
  evidence_id: number
  claim: string
  basis: string
  available_from: number
  sources: CitationSource[]
}

export interface Statement {
  text: string
  evidence_ids: number[]
}

export interface VoiceLine {
  utterance_id: number
  text: string
  available_from: number
}

export interface ChatReply {
  kind: string
  character: { id: number; name: string }
  position: number
  reply: string
  citations: Citation[]
  statements: Statement[]
  unsupported_statements: string[]
  voice_lines: VoiceLine[]
  retrieved_evidence_ids: number[]
}

export class ApiError extends Error {
  status: number
  constructor(status: number, message: string) {
    super(message)
    this.status = status
  }
}

async function request<T>(path: string, init?: RequestInit): Promise<T> {
  let response: Response
  try {
    response = await fetch(`${API_URL}${path}`, init)
  } catch {
    throw new ApiError(0, `Can't reach the companion server at ${API_URL}. Is it running?`)
  }
  if (!response.ok) {
    let message = response.statusText
    try {
      const body = await response.json()
      if (typeof body?.detail === 'string') message = body.detail
    } catch {
      /* not JSON */
    }
    throw new ApiError(response.status, message)
  }
  return response.json() as Promise<T>
}

export const api = {
  books: () => request<Book[]>('/books'),
  book: (id: number) => request<Book>(`/books/${id}`),
  async passages(id: number): Promise<Passage[]> {
    const all: Passage[] = []
    let start: number | null = 1
    while (start !== null) {
      const page: { passages: Passage[]; next_start: number | null } =
        await request(`/books/${id}/passages?start=${start}&limit=1000`)
      all.push(...page.passages)
      start = page.next_start
    }
    return all
  },
  // Display names like "the narrator" start a heading or a list row, so capitalize them.
  characters: (id: number, position: number) =>
    request<Character[]>(`/books/${id}/characters?position=${position}`).then((cast) =>
      cast.map((c) => ({ ...c, name: c.name.charAt(0).toUpperCase() + c.name.slice(1) }))),
  upload(file: File) {
    const form = new FormData()
    form.append('file', file)
    return request<Book>('/books', { method: 'POST', body: form })
  },
  chat(body: { book_id: number; character_id: number; position: number; question: string; history: Turn[] }) {
    return request<ChatReply>('/chat', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify(body),
    })
  },
}
