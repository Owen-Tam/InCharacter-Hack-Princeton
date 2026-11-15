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

/** How much text a page holds, measured in the page's font. */
export interface PageMetrics {
  charsPerLine: number // a rough figure, only for noticing when the layout changes
  lines: number // lines of text per page
  gapLines: number // a paragraph break, in lines
  textWidth: number // px available for a line of text
  indent: number // px of a paragraph's first-line indent
  measure: (text: string) => number // px width of a run of text in the body font
  fontSize: number // px, the body text (the reader's setting)
  headingLines: number // lines a chapter heading takes, with its padding
}

/** Mirror of the .page / .page-body CSS: Lora at the reader's size (16px by default),
 * line height 1.7, 4px paragraph gaps, 1.4em indents, 24px chapter headings. */
export function pageMetrics(viewportWidth: number, viewportHeight: number, fontSize = 16): PageMetrics {
  const narrow = viewportWidth <= 760
  const pageWidth = narrow ? viewportWidth - 112 : 470
  const pageHeight = Math.max(420, Math.min(760, viewportHeight - 200))
  const textWidth = pageWidth - (narrow ? 44 : 88)
  const textHeight = pageHeight - (narrow ? 48 : 88) - 28 // padding, then the page number
  const lineHeight = fontSize * 1.7
  let measure = (text: string) => estimate(text) * (fontSize / 16)
  try {
    const ctx = document.createElement('canvas').getContext('2d')
    if (ctx) {
      ctx.font = `${fontSize}px Lora`
      const cache = new Map<string, number>()
      measure = (text) => {
        let w = cache.get(text)
        if (w === undefined) { w = ctx.measureText(text).width; cache.set(text, w) }
        return w
      }
    }
  } catch {
    /* keep the estimate */
  }
  const sample = 'He looked at me sideways—and I knew why Jordan Baker had believed he was lying, or so it seemed.'
  return {
    charsPerLine: Math.max(20, Math.floor(textWidth / (measure(sample) / sample.length))),
    lines: Math.max(6, Math.floor(textHeight / lineHeight)),
    gapLines: 4 / lineHeight,
    // A hair under the real width, so rounding never makes the browser wrap a line we didn't.
    textWidth: textWidth - 1,
    indent: fontSize * 1.4,
    measure,
    fontSize,
    headingLines: Math.ceil((24 * 1.7 + 12) / lineHeight), // .chapter-heading: 24px at 1.7, 12px padding
  }
}

const estimate = (text: string) => codepoints(text).length * 7.9 // Lora 16px average
const DEFAULT_METRICS: PageMetrics = {
  charsPerLine: 46, lines: 17, gapLines: 0.15, textWidth: 381, indent: 22.4, measure: estimate, fontSize: 16, headingLines: 2,
}

/** A piece of text between two places a line may break: after a space or a dash. */
interface Segment { lo: number; hi: number; width: number; spaceAfter: boolean }

function segments(chars: string[], measure: (s: string) => number): Segment[] {
  const out: Segment[] = []
  let i = 0
  while (i < chars.length) {
    while (i < chars.length && /\s/.test(chars[i])) i++
    if (i >= chars.length) break
    let j = i
    while (j < chars.length && !/\s/.test(chars[j])) {
      j++
      if (/[—–-]/.test(chars[j - 1]) && j < chars.length && !/\s/.test(chars[j])) break
    }
    out.push({ lo: i, hi: j, width: measure(chars.slice(i, j).join('')), spaceAfter: j < chars.length && /\s/.test(chars[j]) })
    i = j
  }
  return out
}

/** Greedy line filling from segment ``from``, as the browser sets justified text:
 * the index just past the last segment of each line. */
function lineBreaks(segs: Segment[], from: number, firstWidth: number, width: number, space: number): number[] {
  const breaks: number[] = []
  let line = -1 // width so far on this line; -1 when it is empty
  let max = firstWidth
  for (let k = from; k < segs.length; k++) {
    const join = k > from && segs[k - 1].spaceAfter ? space : 0
    if (line >= 0 && line + join + segs[k].width > max) {
      breaks.push(k)
      max = width
      line = segs[k].width
    } else {
      line = line < 0 ? segs[k].width : line + join + segs[k].width
    }
  }
  if (line >= 0) breaks.push(segs.length)
  return breaks
}

export function paginate(passages: Passage[], metrics: PageMetrics = DEFAULT_METRICS): Page[] {
  const pages: Page[] = []
  let current: Fragment[] = []
  let used = 0 // lines used on the current page
  const flush = () => {
    if (current.length) pages.push({ fragments: current })
    current = []
    used = 0
  }
  const space = metrics.measure(' ')
  const headings = new Set(findChapters(passages).map((c) => c.paragraphPos))
  for (const passage of passages) {
    const chars = codepoints(passage.text)
    const ends = passage.sentence_ends.length ? passage.sentence_ends : [chars.length]
    if (headings.has(passage.paragraph_pos)) {
      // A chapter heading is set large with space around it; start it on a fresh page
      // unless the page is nearly empty.
      if (used > metrics.lines / 3) flush()
      current.push({ passage, lo: 0, hi: chars.length, text: passage.text.trim(), continued: false, lastSentence: lastSentence(passage) })
      used += (current.length > 1 ? metrics.gapLines : 0) + metrics.headingLines
      continue
    }
    const segs = segments(chars, metrics.measure)
    let from = 0 // first segment not yet placed
    while (from < segs.length) {
      const gap = current.length ? metrics.gapLines : 0
      const avail = Math.floor(metrics.lines - used - gap + 1e-6)
      if (avail < 1) {
        flush()
        continue
      }
      // Only a paragraph's first line is indented; a continuation starts flush.
      const first = from === 0 ? metrics.textWidth - metrics.indent : metrics.textWidth
      const breaks = lineBreaks(segs, from, first, metrics.textWidth, space)
      let take: number
      if (breaks.length <= avail) take = breaks.length
      else if (avail >= 2 || used === 0) take = avail // fill the page, breaking mid-paragraph
      else { flush(); continue } // don't strand a single line at the foot of a page
      const to = breaks[take - 1]
      const lo = from === 0 ? 0 : segs[from].lo
      const hi = to >= segs.length ? chars.length : segs[to - 1].hi
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
      used += gap + take
      from = to
      if (from < segs.length) flush()
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

/** Pages of the current chapter after this spread: up to the page where the next
 * chapter's heading falls (or the end of the book). */
export function pagesLeftInChapter(book: BookModel, spread: Spread): number {
  const lastPage = Math.min(spread.firstPage + 1, book.pages.length)
  const next = book.chapters.find((c) => c.position > spread.position)
  const nextStart = next
    ? book.pages.findIndex((p) => p.fragments.some((f) => f.passage.paragraph_pos === next.paragraphPos))
    : -1
  const chapterEnd = nextStart >= 0 ? nextStart : book.pages.length // pages 1..chapterEnd belong to it
  return Math.max(0, chapterEnd - lastPage)
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

/** The book's author: the stored one, else one packed into the title. */
export function authorOf(book: { title: string; author?: string | null }): string | null {
  return book.author || splitTitle(book.title).author
}

export function toRoman(n: number): string {
  const table: [number, string][] = [[1000, 'M'], [900, 'CM'], [500, 'D'], [400, 'CD'], [100, 'C'], [90, 'XC'], [50, 'L'], [40, 'XL'], [10, 'X'], [9, 'IX'], [5, 'V'], [4, 'IV'], [1, 'I']]
  let out = ''
  for (const [v, s] of table) while (n >= v) { out += s; n -= v }
  return out
}
