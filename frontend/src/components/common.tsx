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

export function Cover({ title, author, large }: { title: string; author?: string | null; large?: boolean }) {
  const t = splitTitle(title)
  return (
    <div className={`cover${large ? ' large' : ''}`} style={{ background: coverColor(title) }} aria-hidden>
      <span className="t">{t.title}</span>
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
