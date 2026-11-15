-- Five tables from the Backend Extraction Guide (pages 11-12).
-- Arrays are stored as JSON text. Foreign keys are enabled per connection in db.connect().

CREATE TABLE IF NOT EXISTS books (
  id INTEGER PRIMARY KEY,
  title text NOT NULL,
  author text,  -- from the EPUB's dc:creator; NULL when it names none
  epub_hash text NOT NULL,
  version text NOT NULL,
  status text NOT NULL CHECK (status IN ('pending','indexing','ready','failed')),
  indexed_through integer NOT NULL DEFAULT 0 CHECK (indexed_through >= 0),
  UNIQUE (epub_hash, version)
);

CREATE TABLE IF NOT EXISTS passages (
  id INTEGER PRIMARY KEY,
  book_id integer NOT NULL REFERENCES books(id),
  paragraph_pos integer NOT NULL,
  href text NOT NULL,
  para_no integer NOT NULL,
  first_sentence integer NOT NULL,
  sentence_ends text NOT NULL CHECK (json_type(sentence_ends) = 'array'),
  text text NOT NULL,
  UNIQUE (book_id, paragraph_pos)
);

CREATE TABLE IF NOT EXISTS characters (
  id INTEGER PRIMARY KEY,
  book_id integer NOT NULL REFERENCES books(id),
  display_name text NOT NULL,
  first_seen integer NOT NULL,
  aliases text NOT NULL DEFAULT '[]',
  voice_snapshots text NOT NULL DEFAULT '[]',
  UNIQUE (book_id, id),
  CHECK (json_type(aliases) = 'array' AND json_type(voice_snapshots) = 'array')
);

CREATE TABLE IF NOT EXISTS utterances (
  id INTEGER PRIMARY KEY,
  book_id integer NOT NULL REFERENCES books(id),
  speaker_id integer,
  addressee_id integer,
  available_from integer NOT NULL,
  sources text NOT NULL CHECK (json_type(sources) = 'array'),
  FOREIGN KEY (book_id, speaker_id) REFERENCES characters(book_id, id),
  FOREIGN KEY (book_id, addressee_id) REFERENCES characters(book_id, id)
);

CREATE TABLE IF NOT EXISTS evidence (
  id INTEGER PRIMARY KEY,
  book_id integer NOT NULL REFERENCES books(id),
  character_id integer NOT NULL,
  claim text NOT NULL,
  basis text NOT NULL CHECK (basis IN
    ('observed','performed_action','said','was_told','private_thought')),
  reported_by_character_id integer,
  available_from integer NOT NULL,
  sources text NOT NULL CHECK (json_type(sources) = 'array'),
  FOREIGN KEY (book_id, character_id) REFERENCES characters(book_id, id),
  FOREIGN KEY (book_id, reported_by_character_id) REFERENCES characters(book_id, id)
);

CREATE INDEX IF NOT EXISTS evidence_lookup ON evidence(book_id, character_id, available_from);
CREATE INDEX IF NOT EXISTS dialogue_lookup ON utterances(book_id, speaker_id, available_from);
