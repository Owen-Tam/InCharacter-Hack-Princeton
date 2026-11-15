import type { ReactNode } from 'react'
import { Bookmark } from 'lucide-react'
import { splitTitle } from '../book'

export function TopBar({ title, chapter, children, onBrand }: {
  title?: string
  chapter?: string | null
  children?: ReactNode
  onBrand: () => void
}) {
  return (
    <header className="bar">
      <button className="brand" onClick={onBrand}>InCharacter</button>
      {title && (
        <span className="bar-title">
          {splitTitle(title).title} {chapter && <em>· {chapter}</em>}
        </span>
      )}
      <nav className="bar-actions">{children}</nav>
    </header>
  )
}

export function BookmarkRule({ label }: { label?: string }) {
  return (
    <div className="bookmark-rule" role="separator">
      {label ? <span>{label}</span> : <Bookmark size={13} strokeWidth={1.6} aria-label="Your bookmark" />}
    </div>
  )
}

const HUES = [260, 200, 30, 150, 330, 80, 290]

export function coverColor(title: string): string {
  let h = 0
  for (const ch of title) h = (h * 31 + ch.charCodeAt(0)) >>> 0
  return `oklch(0.40 0.07 ${HUES[h % HUES.length]})`
}

// Cover titles never break inside a word: the size comes down until the longest word
// fits the cover's width and every line fits its height.
let measureCtx: CanvasRenderingContext2D | null | undefined
function wordWidth(text: string, size: number): number {
  if (measureCtx === undefined) {
    try { measureCtx = document.createElement('canvas').getContext('2d') } catch { measureCtx = null }
  }
  if (!measureCtx) return text.length * size * 0.5
  measureCtx.font = `${size}px "Cormorant Garamond", Georgia, serif` // .cover .t
  return measureCtx.measureText(text).width
}

const fitted = new Map<string, number>()
function fitTitle(title: string, base: number, width: number, height: number): number {
  const key = `${title}|${base}`
  const hit = fitted.get(key)
  if (hit) return hit
  const words = title.split(/\s+/).filter(Boolean)
  let size = base
  for (; size > 10; size -= 0.5) {
    const space = wordWidth(' ', size)
    let lines = 1
    let line = 0
    let fits = true
    for (const word of words) {
      const w = wordWidth(word, size)
      if (w > width) { fits = false; break }
      if (line && line + space + w > width) { lines++; line = w } else line = line ? line + space + w : w
    }
    if (fits && lines * size * 1.05 <= height) break
  }
  fitted.set(key, size)
  return size
}

export function Cover({ title, author, large }: { title: string; author?: string | null; large?: boolean }) {
  const t = splitTitle(title)
  // Room inside the cover's padding, less the author line at the foot (see .cover CSS).
  const fontSize = large ? fitTitle(t.title, 25, 116, 150) : fitTitle(t.title, 19, 84, 100)
  return (
    <div className={`cover${large ? ' large' : ''}`} style={{ background: coverColor(title) }} aria-hidden>
      <span className="t" style={{ fontSize }}>{t.title}</span>
      <div className="foot">
        <div className="rule" />
        <span className="a">{author || t.author || ' '}</span>
      </div>
    </div>
  )
}

/** Paint [lo, hi) code-point ranges of `text` as highlights. */
export function Highlighted({ text, ranges }: { text: string; ranges: [number, number][] }) {
  const chars = Array.from(text)
  const sorted = [...ranges].filter(([lo, hi]) => hi > lo).sort((a, b) => a[0] - b[0])
  const out: ReactNode[] = []
  let at = 0
  sorted.forEach(([lo, hi], i) => {
    lo = Math.max(lo, at)
    if (hi <= lo) return
    if (lo > at) out.push(chars.slice(at, lo).join(''))
    out.push(<mark className="cite" key={i}>{chars.slice(lo, hi).join('')}</mark>)
    at = hi
  })
  if (at < chars.length) out.push(chars.slice(at).join(''))
  return <>{out}</>
}
