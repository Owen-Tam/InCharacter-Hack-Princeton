"""Explicit book/character voice assignments and on-demand speech."""
import json
import os
from elevenlabs import ElevenLabs
from dotenv import load_dotenv
from . import config

# Match canonical identities, not database IDs, so reimports retain the assignment.
VOICE_ASSIGNMENTS = {
    ('the diary of a young girl: the definitive edition', 'anne frank'): 'PV4xbyCf6QubEtfcEidV',
}


def assigned_voice(book, character):
    names = [character['display_name'], *[a['name'] for a in json.loads(character['aliases'])]]
    for name in names:
        voice = VOICE_ASSIGNMENTS.get((book['title'].strip().casefold(), name.strip().casefold()))
        if voice:
            return voice
    return None


def synthesize(voice_id: str, text: str) -> bytes:
    load_dotenv(config.DATA_DIR.parent.parent / ".env")
    if not os.environ.get('ELEVENLABS_API_KEY'):
        raise RuntimeError('Speech is not configured on the server.')
    client = ElevenLabs()
    return b''.join(client.text_to_speech.convert(
        voice_id=voice_id, text=text, model_id='eleven_v4', output_format='mp3_44100_128',
    ))
