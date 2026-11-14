from companion import db
from companion.resolve import resolve_chunk
from companion.commit import commit_chunk

conn = db.connect("scratch.db"); db.init_schema(conn)
book = db.create_book(conn, "The Missing Letter", "demo", "extract-v1")
text = "Theo stepped outside. Mara hid the letter in the atlas. Theo returned and said, \u201cEmpty again.\u201d Mara showed him the letter inside the atlas."
if not db.load_passages(conn, book):
    db.insert_passage(conn, book, paragraph_pos=1, href="ch01.xhtml", para_no=0,
                      first_sentence=1, sentence_ends=[21, 55, 94, 139], text=text)

candidates = {
  "characters": [{"key": "theo", "name": "Theo", "first_mention": {"para": 1, "text": "Theo stepped outside"}}],
  "utterances": [{"speaker": "theo", "quotes": [{"para": 1, "text": "Empty again."}],
                  "attribution": [{"para": 1, "text": "Theo returned and said"}]},
                 {"speaker": "theo", "quotes": [{"para": 1, "text": "I saw Mara hide it."}]}],
}
chunk = resolve_chunk(candidates, db.load_passages(conn, book), db.load_characters(conn, book), 0, 4)
print("rejects:", chunk.rejects)
print(commit_chunk(conn, book, chunk))
