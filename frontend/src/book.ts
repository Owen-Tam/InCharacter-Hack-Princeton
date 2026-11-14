// The book as the reader sees it: pages, spreads, chapters and the bookmark.
//
// Offsets from the backend count Unicode code points, so every slice here goes
// through Array.from(text) rather than String.prototype.slice.

import type { Passage } from './api'

/** A run of whole sentences from one paragraph, placed on one page. */
export interface Fragment {
  passage: Passage
  lo: number // code-point offset where this fragment starts
  hi: number // code-point offset where it ends
  text: string
  continued: boolean // the paragraph began on an earlier page
  runsOn?: boolean // the paragraph continues on the next page
  lastSentence: number // book-wide number of the last sentence that ends here
}

export interface Page {
  fragments: Fragment[]
}

export interface Spread {
  index: number
  pages: [Page, Page | null]
  firstPage: number // 1-based page number of the left page
  position: number // last sentence finished at the end of this spread (the bookmark)
}

export interface Chapter {
  label: string // e.g. "IV" or "Chapter 4"
  paragraphPos: number
  position: number // sentence where the chapter starts
}

export interface BookModel {
  passages: Passage[]
  byId: Map<number, Passage>
  pages: Page[]
  spreads: Spread[]
  chapters: Chapter[]
  totalSentences: number
}

export function codepoints(text: string): string[] {
  return Array.from(text)
}

export function lastSentence(p: Passage): number {
  return p.first_sentence + p.sentence_ends.length - 1
}

/** How much text a page holds, estimated from the page's size and the body font. */
export interface PageMetrics {
  charsPerLine: number
  lines: number // lines of text per page
  gapLines: number // a paragraph break, in lines
}

/** Mirror of the .page / .page-body CSS: 16px Lora at 1.7, 14px paragraph gaps. */
export function pageMetrics(viewportWidth: number, viewportHeight: number): PageMetrics {
  const narrow = viewportWidth <= 760
  const pageWidth = narrow ? viewportWidth - 112 : 470
  const pageHeight = Math.max(420, Math.min(600, viewportHeight - 200))
  const textWidth = pageWidth - (narrow ? 44 : 88)
  const textHeight = pageHeight - (narrow ? 48 : 88) - 28 // padding, then the page number
  const lineHeight = 16 * 1.7
  let charWidth = 7.9 // Lora 16px average, used until the font has loaded
  try {
    const ctx = document.createElement('canvas').getContext('2d')
    if (ctx) {
      ctx.font = '16px Lora'
      const sample = 'He looked at me sideways—and I knew why Jordan Baker had believed he was lying, or so it seemed.'
      charWidth = ctx.measureText(sample).width / sample.length
    }
  } catch {
    /* keep the estimate */
  }
  return {
    charsPerLine: Math.max(20, Math.floor((textWidth / charWidth) * 0.97)),
    lines: Math.max(6, Math.floor(textHeight / lineHeight)),
    gapLines: 14 / lineHeight,
  }
}

const DEFAULT_METRICS: PageMetrics = { charsPerLine: 46, lines: 17, gapLines: 0.5 }

export function paginate(passages: Passage[], metrics: PageMetrics = DEFAULT_METRICS): Page[] {
  const pages: Page[] = []
  let current: Fragment[] = []
  let used = 0 // lines used on the current page
  const flush = () => {
    if (current.length) pages.push({ fragments: current })
    current = []
    used = 0
  }
  const cpl = metrics.charsPerLine
  // Lines a run of text takes; the first line of a paragraph is indented.
  const linesFor = (chars: number) => Math.ceil((chars + 3) / cpl)
  const headings = new Set(findChapters(passages).map((c) => c.paragraphPos))
  for (const passage of passages) {
    const chars = codepoints(passage.text)
    const ends = passage.sentence_ends.length ? passage.sentence_ends : [chars.length]
    if (headings.has(passage.paragraph_pos)) {
      // A chapter heading is set large with space around it; start it on a fresh page
      // unless the page is nearly empty.
      if (used > metrics.lines / 3) flush()
      current.push({ passage, lo: 0, hi: chars.length, text: passage.text.trim(), continued: false, lastSentence: lastSentence(passage) })
      used += (current.length > 1 ? metrics.gapLines : 0) + 3
      continue
    }
    let lo = 0
    while (lo < chars.length) {
      const gap = current.length ? metrics.gapLines : 0
      const avail = Math.floor(metrics.lines - used - gap)
      if (avail < 1) {
        flush()
        continue
      }
      const capacity = avail * cpl - 3
      let hi: number
      if (chars.length - lo <= capacity) {
        hi = chars.length
      } else {
        // Break at the last sentence end that fills most of the room, else at a word,
        // the way a printed page breaks mid-sentence.
        const limit = lo + capacity
        const sentenceEnd = [...ends].reverse().find((e) => e > lo && e <= limit)
        let wordEnd = -1
        for (let k = limit; k > lo; k--) if (chars[k] === ' ') { wordEnd = k; break }
        if (sentenceEnd !== undefined && sentenceEnd - lo >= capacity * 0.8) hi = sentenceEnd
        else if (wordEnd > lo && (avail >= 2 || used === 0)) hi = wordEnd
        else if (used > 0) { flush(); continue }
        else hi = limit
      }
      const done = ends.filter((e) => e <= hi).length
      current.push({
        passage,
        lo,
        hi,
        text: chars.slice(lo, hi).join('').trim(),
        continued: lo > 0,
        runsOn: hi < chars.length,
        // The bookmark counts finished sentences only; a sentence split across pages
        // belongs to the page where it ends.
        lastSentence: passage.first_sentence + done - 1,
      })
      used += gap + linesFor(hi - lo)
      lo = hi
      while (lo < chars.length && chars[lo] === ' ') lo++
      if (lo < chars.length) flush()
    }
  }
  flush()
  return pages
}

const HEADING = /^(chapter\s+)?([ivxlc]+|\d{1,3})\.?$|^chapter\s+[a-z-]+$/i

export function findChapters(passages: Passage[]): Chapter[] {
  const chapters: Chapter[] = []
  for (let i = 0; i < passages.length - 1; i++) {
    const text = passages[i].text.trim()
    // A heading is followed by real prose, which keeps a table of contents out.
    if (text.length <= 24 && HEADING.test(text) && passages[i + 1].text.length > 80) {
      chapters.push({ label: text.replace(/\.$/, ''), paragraphPos: passages[i].paragraph_pos, position: passages[i].first_sentence })
    }
  }
  return chapters
}

export function buildBook(passages: Passage[], metrics?: PageMetrics): BookModel {
  const pages = paginate(passages, metrics)
  const spreads: Spread[] = []
  let position = 0
  for (let i = 0; i < pages.length; i += 2) {
    const pair: [Page, Page | null] = [pages[i], pages[i + 1] ?? null]
    for (const page of pair) for (const f of page?.fragments ?? []) position = Math.max(position, f.lastSentence)
    spreads.push({ index: spreads.length, pages: pair, firstPage: i + 1, position })
  }
  const last = passages[passages.length - 1]
  return {
    passages,
    byId: new Map(passages.map((p) => [p.id, p])),
    pages,
    spreads,
    chapters: findChapters(passages),
    totalSentences: last ? lastSentence(last) : 0,
  }
}

/** The spread a bookmark falls on: the first whose end reaches the position. */
export function spreadForPosition(book: BookModel, position: number): Spread {
  return book.spreads.find((s) => s.position >= position) ?? book.spreads[book.spreads.length - 1]
}

export function spreadForPassage(book: BookModel, passageId: number): Spread {
  const found = book.spreads.find((s) => s.pages.some((p) => p?.fragments.some((f) => f.passage.id === passageId)))
  return found ?? book.spreads[0]
}

export function chapterAt(book: BookModel, position: number): Chapter | null {
  let found: Chapter | null = null
  for (const c of book.chapters) {
    if (c.position <= position) found = c
    else break
  }
  return found
}

export function chapterLabel(book: BookModel, position: number): string | null {
  const c = chapterAt(book, position)
  if (!c) return null
  return /^chapter/i.test(c.label) ? c.label : `Chapter ${c.label}`
}

export function shortChapter(book: BookModel, position: number): string | null {
  const c = chapterAt(book, position)
  return c ? `Ch. ${c.label.replace(/^chapter\s+/i, '')}` : null
}

/** Paragraph text as far as the bookmark, for the "where you left off" panels. */
export function textUpTo(passage: Passage, position: number): string {
  if (lastSentence(passage) <= position) return passage.text
  const end = passage.sentence_ends[position - passage.first_sentence]
  return codepoints(passage.text).slice(0, end).join('')
}

/** The last few paragraphs the reader finished, ending at the bookmark. */
export function recentParagraphs(book: BookModel, position: number, count: number): { passage: Passage; text: string }[] {
  const out: { passage: Passage; text: string }[] = []
  for (let i = book.passages.length - 1; i >= 0 && out.length < count; i--) {
    const p = book.passages[i]
    if (p.first_sentence > position) continue
    const text = textUpTo(p, position).trim()
    if (text) out.unshift({ passage: p, text })
  }
  return out
}

/** A display name inside a sentence: "the narrator", not "The narrator". */
export function inSentence(name: string): string {
  return /^The\s/.test(name) ? 't' + name.slice(1) : name
}

/** "The Lottery--Shirley Jackson" carries its author in the title. */
export function splitTitle(raw: string): { title: string; author: string | null } {
  const m = raw.split(/\s*--\s*|\s+—\s+/)
  if (m.length === 2 && m[0] && m[1]) return { title: m[0], author: m[1] }
  return { title: raw.replace(/^[a-z]/, (c) => c.toUpperCase()), author: null }
}

export function toRoman(n: number): string {
  const table: [number, string][] = [[1000, 'M'], [900, 'CM'], [500, 'D'], [400, 'CD'], [100, 'C'], [90, 'XC'], [50, 'L'], [40, 'XL'], [10, 'X'], [9, 'IX'], [5, 'V'], [4, 'IV'], [1, 'I']]
  let out = ''
  for (const [v, s] of table) while (n >= v) { out += s; n -= v }
  return out
}
