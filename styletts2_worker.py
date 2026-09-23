"""Optional local Ukrainian StyleTTS2 worker; dependencies stay out of the GUI."""
import json
from pathlib import Path
import re
import sys
import textwrap
from unicodedata import normalize
from ukrainian_numbers import expand_numbers

CATALOG = json.loads(Path(__file__).with_name('styletts2_voices.json').read_text())


def text_chunks(text):
    text = normalize('NFKC', text.replace('+', '\u0301').replace('"', ''))
    text = re.sub(r'[᠆‐‑‒–—―⁻₋−⸺⸻]', '-', text).replace(' - ', ': ')
    for sentence in re.split(r'(?<=[.!?:])\s+', text):
        for part in textwrap.wrap(sentence, width=180, break_long_words=True, break_on_hyphens=False):
            yield part if part[-1] in '.?!:-' else part + '.'


class Synthesizer:
    def __init__(self, device):
        import torch
        from ipa_uk import ipa
        from styletts2_inference.models import StyleTTS2
        from ukrainian_word_stress import Stressifier
        if device == 'Auto':
            device = 'cuda' if torch.cuda.is_available() else 'cpu'
        else:
            device = device.lower()
        if device == 'cuda' and not torch.cuda.is_available():
            raise RuntimeError('CUDA is unavailable in this environment; select CPU or Auto.')
        self.torch, self.ipa = torch, ipa
        self.stress = Stressifier()
        self.model = StyleTTS2(hf_path='patriotyk/styletts2_ukrainian_multispeaker_hifigan', device=device)
        self.device = device
        self.styles = {}

    def generate(self, request):
        import soundfile
        from huggingface_hub import hf_hub_download
        voice = request['voice']
        if voice not in CATALOG['voices']:
            raise ValueError('Unknown StyleTTS2 voice preset')
        if voice not in self.styles:
            path = hf_hub_download(CATALOG['repo'], 'voices/' + voice + '.pt',
                repo_type='space', revision=CATALOG['revision'])
            self.styles[voice] = self.torch.load(path, weights_only=True, map_location=self.device)
        audio = []
        with self.torch.inference_mode():
            for part in text_chunks(expand_numbers(request['text'], request.get('numbers', True))):
                phonemes = self.ipa(self.stress(part))
                tokens = self.model.tokenizer.encode(phonemes)
                if tokens.numel():
                    audio.append(self.model(tokens, speed=request['speed'], s_prev=self.styles[voice]).detach().cpu().reshape(-1))
        if not audio:
            raise ValueError('No pronounceable Ukrainian text; use aliases for foreign nicknames.')
        soundfile.write(request['output'], self.torch.cat(audio).numpy(), 24000, subtype='PCM_16')


def main():
    model = None
    for line in sys.stdin:
        request = json.loads(line)
        try:
            if model is None:
                model = Synthesizer(request['device'])
            model.generate(request)
            result = {'ok': True}
        except Exception as error:
            result = {'error': f'StyleTTS2: {type(error).__name__}: {error}'}
        target = Path(request['reply'])
        temporary = target.with_suffix('.tmp')
        temporary.write_text(json.dumps(result))
        temporary.replace(target)


if __name__ == '__main__':
    main()
