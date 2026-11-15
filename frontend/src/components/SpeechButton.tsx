import { useEffect, useRef, useState } from 'react'
import { LoaderCircle, Square, Volume2 } from 'lucide-react'
import { API_URL } from '../api'

// Only one reply plays at a time, including across separate message buttons.
let currentAudio: HTMLAudioElement | null = null

export function SpeechButton({ bookId, characterId, position, text }: {
  bookId: number; characterId: number; position: number; text: string
}) {
  const [state, setState] = useState<'idle' | 'loading' | 'playing'>('idle')
  const [error, setError] = useState('')
  const audio = useRef<HTMLAudioElement | null>(null)
  const url = useRef<string | null>(null)
  const pending = useRef<AbortController | null>(null)

  useEffect(() => () => {
    pending.current?.abort()
    audio.current?.pause()
    if (currentAudio === audio.current) currentAudio = null
    if (url.current) URL.revokeObjectURL(url.current)
  }, [])

  async function play() {
    if (state === 'loading') return
    if (state === 'playing') { audio.current?.pause(); return }
    setError('')
    setState('loading')
    const controller = new AbortController()
    pending.current = controller
    try {
      if (!audio.current) {
        const response = await fetch(`${API_URL}/speech`, {
          method: 'POST', headers: { 'Content-Type': 'application/json' },
          body: JSON.stringify({ book_id: bookId, character_id: characterId, position, text }),
          signal: controller.signal,
        })
        if (!response.ok) throw new Error('Unable to generate audio. Click to retry.')
        const blob = await response.blob()
        if (controller.signal.aborted) return
        url.current = URL.createObjectURL(blob)
        audio.current = new Audio(url.current)
        audio.current.onended = () => setState('idle')
        audio.current.onpause = () => setState('idle')
        audio.current.onerror = () => {
          setState('idle')
          setError('Unable to play audio. Click to retry.')
        }
      }
      currentAudio?.pause()
      currentAudio = audio.current
      audio.current.currentTime = 0
      await audio.current.play()
      if (!controller.signal.aborted) setState('playing')
    } catch {
      if (!controller.signal.aborted) {
        setState('idle')
        setError('Unable to play audio. Click to retry.')
      }
    }
  }

  const label = state === 'playing' ? 'Stop audio' : state === 'loading' ? 'Preparing audio' : 'Listen to this reply'
  return <>
    <button className="link-quiet speech-button" type="button" onClick={play}
      disabled={state === 'loading'} aria-label={label} title={`${label} · AI-generated voice`} aria-busy={state === 'loading'}>
      {state === 'loading' ? <LoaderCircle size={15} /> : state === 'playing' ? <Square size={15} /> : <Volume2 size={15} />}
    </button>
    {error && <span role="alert" className="unsupported">{error}</span>}
  </>
}
