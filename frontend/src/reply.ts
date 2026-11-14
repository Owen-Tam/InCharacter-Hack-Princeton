// Place citation notes in a reply. The backend returns the reply text plus
// `statements` (each with evidence ids) that paraphrase parts of it; each
// statement is matched to the reply sentence it overlaps most.

import type { ChatReply } from './api'
import { shortChapter, type BookModel } from './book'

export interface ReplySentence {
  text: string
  notes: number[] // indexes into reply.citations
}

const words = (s: string) =>
  new Set(s.toLowerCase().normalize('NFKD').replace(/[^\p{L}\p{N}\s]/gu, ' ').split(/\s+/).filter((w) => w.length > 2))

export function splitSentences(text: string): string[] {
  const parts = text.match(/[^.!?…]+(?:[.!?…]+["”’)]*|$)\s*/g) ?? [text]
  return parts.filter((p) => p.trim())
}

export function annotate(reply: ChatReply): ReplySentence[] {
  const sentences: ReplySentence[] = splitSentences(reply.reply).map((text) => ({ text, notes: [] }))
  if (!sentences.length) return sentences
  const noteFor = new Map(reply.citations.map((c, i) => [c.evidence_id, i]))
  const sentenceWords = sentences.map((s) => words(s.text))
  for (const statement of reply.statements) {
    const notes = statement.evidence_ids.map((id) => noteFor.get(id)).filter((n): n is number => n !== undefined)
    if (!notes.length) continue
    const sw = words(statement.text)
    let best = sentences.length - 1
    let bestScore = 0
    sentenceWords.forEach((ws, i) => {
      let shared = 0
      for (const w of sw) if (ws.has(w)) shared++
      const score = shared / Math.max(1, Math.min(sw.size, ws.size))
      if (score > bestScore) { bestScore = score; best = i }
    })
    for (const n of notes) if (!sentences[best].notes.includes(n)) sentences[best].notes.push(n)
  }
  for (const s of sentences) s.notes.sort((a, b) => a - b)
  return sentences
}

/** "2 sources · Ch. III, IV" */
export function sourcesLabel(reply: ChatReply, model: BookModel): string | null {
  const n = reply.citations.length
  if (!n) return null
  const chapters: string[] = []
  for (const c of reply.citations) {
    for (const s of c.sources) {
      const p = model.byId.get(s.passage_id)
      const ch = p && shortChapter(model, p.first_sentence)
      const label = ch?.replace(/^Ch\. /, '')
      if (label && !chapters.includes(label)) chapters.push(label)
    }
  }
  const head = n === 1 ? '1 source' : `${n} sources`
  return chapters.length ? `${head} · Ch. ${chapters.join(', ')}` : head
}

export const BASIS: Record<string, string> = {
  observed: 'They saw it',
  performed_action: 'They did it',
  said: 'They said it',
  was_told: 'They were told',
  private_thought: 'Their own thought',
}
