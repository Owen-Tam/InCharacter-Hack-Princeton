import { useEffect, useState } from 'react'

export type Route =
  | { screen: 'library' }
  | { screen: 'read'; bookId: number }
  | { screen: 'cast'; bookId: number }
  | { screen: 'chat'; bookId: number; characterId: number }
  | { screen: 'compare'; bookId: number; characterId: number }

export function parse(hash: string): Route {
  const parts = hash.replace(/^#\/?/, '').split('/').filter(Boolean)
  const bookId = Number(parts[1])
  if (parts[0] === 'book' && Number.isFinite(bookId)) {
    const characterId = Number(parts[3])
    if (parts[2] === 'cast') return { screen: 'cast', bookId }
    if (parts[2] === 'talk' && Number.isFinite(characterId)) {
      return parts[4] === 'compare' ? { screen: 'compare', bookId, characterId } : { screen: 'chat', bookId, characterId }
    }
    return { screen: 'read', bookId }
  }
  return { screen: 'library' }
}

export function href(route: Route): string {
  switch (route.screen) {
    case 'library': return '#/'
    case 'read': return `#/book/${route.bookId}`
    case 'cast': return `#/book/${route.bookId}/cast`
    case 'chat': return `#/book/${route.bookId}/talk/${route.characterId}`
    case 'compare': return `#/book/${route.bookId}/talk/${route.characterId}/compare`
  }
}

export function go(route: Route) {
  window.location.hash = href(route)
}

export function useRoute(): Route {
  const [route, setRoute] = useState(() => parse(window.location.hash))
  useEffect(() => {
    const onChange = () => setRoute(parse(window.location.hash))
    window.addEventListener('hashchange', onChange)
    return () => window.removeEventListener('hashchange', onChange)
  }, [])
  return route
}
