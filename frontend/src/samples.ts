// Display-only books that fill out the library shelf. They have no text on the
// backend and can't be opened; adding the real EPUB imports the book properly.

export interface SampleBook {
  key: string
  title: string
  author: string
}

export const SAMPLE_BOOKS: SampleBook[] = [
  { key: 'pride-and-prejudice', title: 'Pride and Prejudice', author: 'Jane Austen' },
  { key: 'tale-of-two-cities', title: 'A Tale of Two Cities', author: 'Charles Dickens' },
  { key: 'crime-and-punishment', title: 'Crime and Punishment', author: 'Fyodor Dostoevsky' },
  { key: 'frankenstein', title: 'Frankenstein', author: 'Mary Shelley' },
  { key: 'jekyll-and-hyde', title: 'Dr. Jekyll and Mr. Hyde', author: 'Robert Louis Stevenson' },
  { key: 'animal-farm', title: 'Animal Farm', author: 'George Orwell' },
  { key: 'great-expectations', title: 'Great Expectations', author: 'Charles Dickens' },
  { key: 'mistborn', title: 'Mistborn: The Final Empire', author: 'Brandon Sanderson' },
  { key: 'philosophers-stone', title: 'Harry Potter and the Philosopher’s Stone', author: 'J.K. Rowling' },
]
