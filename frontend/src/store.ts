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

// -- display settings -----------------------------------------------------------

export type Theme = 'dark' | 'light'
export type Contrast = 'normal' | 'high'

export interface Settings {
  theme: Theme
  contrast: Contrast
  bookFont: number // px, the book pages' body text
}

export const BOOK_FONT_SIZES = [14, 16, 18, 20, 22]
const DEFAULT_SETTINGS: Settings = { theme: 'dark', contrast: 'normal', bookFont: 16 }
const SETTINGS_KEY = 'incharacter.settings.v1'

function loadSettings(): Settings {
  try {
    const saved = JSON.parse(localStorage.getItem(SETTINGS_KEY) ?? '{}') ?? {}
    return {
      theme: saved.theme === 'light' ? 'light' : 'dark',
      contrast: saved.contrast === 'high' ? 'high' : 'normal',
      bookFont: BOOK_FONT_SIZES.includes(saved.bookFont) ? saved.bookFont : DEFAULT_SETTINGS.bookFont,
    }
  } catch {
    return DEFAULT_SETTINGS
  }
}

let settings = loadSettings()
const listeners = new Set<() => void>()

/** Put the settings on the page: theme and contrast as attributes on <html> (the
 * CSS tokens switch on them), the book's text size as a custom property. */
export function applySettings(s: Settings = settings) {
  const root = document.documentElement
  root.dataset.theme = s.theme
  root.dataset.contrast = s.contrast
  root.style.setProperty('--book-font', `${s.bookFont}px`)
}

export function getSettings(): Settings {
  return settings
}

export function updateSettings(patch: Partial<Settings>) {
  settings = { ...settings, ...patch }
  applySettings(settings)
  try {
    localStorage.setItem(SETTINGS_KEY, JSON.stringify(settings))
  } catch {
    /* storage unavailable: the change still holds for this visit */
  }
  listeners.forEach((l) => l())
}

export function subscribeSettings(listener: () => void): () => void {
  listeners.add(listener)
  return () => listeners.delete(listener)
}
