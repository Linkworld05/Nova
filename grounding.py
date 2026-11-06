# SPDX-License-Identifier: AGPL-3.0-or-later
"""Conservative citation and numeric checks, not a claim of full fact verification."""
import re

CITATION = re.compile(r'\[M(\d+)\s+p\.\s*(\d+)\]')
QUANTITY = re.compile(r'(?i)(?<![\w.])\d+(?:[.,]\d+)?\s*(?:kpa|mpa|psi|bar|volts?|[vaw]|hz|khz|mhz|ghz|°[cf]|mm|cm|km|ms|seconds?|segundos?|minutes?|minutos?|hours?|horas?|mb|gb|tb|%)(?!\w)')


def issues(answer, sources):
    valid = {s['label'] for s in sources}
    found = {f'[M{m.group(1)} p.{m.group(2)}]' for m in CITATION.finditer(answer)}
    problems = []
    if found - valid:
        problems.append('citation points to an excerpt that was not supplied')
    # A clear statement that no answer was found is allowed without citations.
    refusal = re.search(r"(?i)(couldn.t find|cannot (?:find|confirm)|not (?:found|provided|specified)|no (?:encontr[eé]|puedo confirmar|se (?:indica|especifica))|excerpts (?:do not|don.t)|fragmentos no)", answer)
    if not found and not refusal:
        problems.append('manual answer has no supplied page citation')
    text = '\n'.join(s['text'] for s in sources)
    normalize = lambda v: re.sub(r'\s+', '', v.casefold()).replace(',', '.')
    supported = {normalize(m.group()) for m in QUANTITY.finditer(text)}
    for match in QUANTITY.finditer(answer):
        if normalize(match.group()) not in supported:
            problems.append('numeric value or unit is absent from retrieved text')
            break
    return problems


def checked_answer(answer, sources, question, retry):
    problems = issues(answer, sources)
    if not problems:
        return answer
    repaired = retry('; '.join(problems))
    if repaired and not issues(repaired, sources):
        return repaired
    spanish = bool(re.search(r'(?i)[¿áéíóúñ]|\b(c[oó]mo|qu[eé]|puedo|manual|explica|dame)\b', question))
    return ('No puedo confirmar esas instrucciones con los fragmentos recuperados del manual. Indica la sección o página para revisarla; no usaré cifras ni referencias sin respaldo.' if spanish else
            'I cannot confirm those instructions from the retrieved manual excerpts. Please specify the section or page to check; unsupported values and page references were withheld.')
