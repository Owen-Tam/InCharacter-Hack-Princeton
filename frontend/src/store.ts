// Per-reader conveniences kept in the browser: where each book was left, and
// which book was opened last. Every access is guarded; the app works without it.

export interface ReadingState {
  position: number // the bookmark: last sentence finished
  page: number // left page of the spread, for the library's "continue on page N"
  openedAt: number
}

const KEY = 'incharacter.reading.v1'

function load(): Record<string, ReadingState> {
  try {
    return JSON.parse(localStorage.getItem(KEY) ?? '{}') ?? {}
  } catch {
    return {}
  }
}

export function readingState(bookId: number): ReadingState | null {
  return load()[bookId] ?? null
}

export function allReadingStates(): Record<string, ReadingState> {
  return load()
}

export function saveReadingState(bookId: number, state: Omit<ReadingState, 'openedAt'>) {
  try {
    const all = load()
    all[bookId] = { ...state, openedAt: Date.now() }
    localStorage.setItem(KEY, JSON.stringify(all))
  } catch {
    /* storage unavailable */
  }
}
