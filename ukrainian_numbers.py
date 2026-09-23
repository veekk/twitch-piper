"""Optional, deterministic Ukrainian number reading before phonemization."""
import re

NUMBER = re.compile(r'(?<!\w)[−-]?(?:[0-9]{1,3}(?:[ \u00a0\u202f][0-9]{3})+(?![0-9])|[0-9]+)(?:[.,][0-9]+)*|[0-9]+')


def expand_numbers(text, enabled=True):
    if not enabled or not re.search(r'[0-9]', text):
        return text
    try:
        from num2words import num2words
    except ImportError as error:
        raise RuntimeError('Number reading needs num2words in the StyleTTS2 Python environment; install requirements-styletts2.txt.') from error

    def integer(value):
        # Preserve codes/leading zeros and keep huge numeric strings bounded.
        if len(value) > 15 or (len(value) > 1 and value.startswith('0')):
            return ' '.join(num2words(int(digit), lang='uk') for digit in value)
        return num2words(int(value), lang='uk')

    def replace(match):
        raw = re.sub(r'[ \u00a0\u202f]', '', match.group())
        negative = raw.startswith(('-', '−'))
        raw = raw.lstrip('-−')
        pieces = re.split(r'([.,])', raw)
        words = integer(pieces[0])
        for index in range(1, len(pieces), 2):
            # Decimals are deliberately spoken digit by digit, preserving zeros.
            separator = 'кома' if len(pieces) == 3 or pieces[index] == ',' else 'крапка'
            tail = ' '.join(num2words(int(digit), lang='uk') for digit in pieces[index + 1])
            words += ' ' + separator + ' ' + tail
        if negative:
            words = 'мінус ' + words
        # Separate digits embedded in usernames/unit abbreviations for the phonemizer.
        if match.start() and text[match.start()-1].isalnum():
            words = ' ' + words
        if match.end() < len(text) and text[match.end()].isalnum():
            words += ' '
        return words

    return NUMBER.sub(replace, text)
