import json
from companion.speech import assigned_voice, synthesize


def test_voice_is_scoped_to_book_and_character():
    book = {'title': 'The diary of a young girl: the definitive edition'}
    anne = {'display_name': 'Anne', 'aliases': json.dumps([{'name': 'Anne Frank'}])}
    assert assigned_voice(book, anne) == 'PV4xbyCf6QubEtfcEidV'
    assert assigned_voice({'title': 'The Great Gatsby'}, anne) is None
    assert assigned_voice(book, {'display_name': 'Margot Frank', 'aliases': '[]'}) is None


def test_speech_uses_designated_voice_without_real_api(monkeypatch):
    from types import SimpleNamespace
    from companion import speech
    calls = []
    def convert(**kwargs):
        calls.append(kwargs)
        return iter([b'first', b'second'])
    monkeypatch.setattr(speech, 'load_dotenv', lambda path: None)
    monkeypatch.setenv('ELEVENLABS_API_KEY', 'test-only')
    monkeypatch.setattr(speech, 'ElevenLabs', lambda: SimpleNamespace(text_to_speech=SimpleNamespace(convert=convert)))
    assert synthesize('designated-voice', 'Hello') == b'firstsecond'
    assert calls[0]['voice_id'] == 'designated-voice'
    assert calls[0]['text'] == 'Hello'
