"""Independent 80-digit probability oracle; synthetic test inputs only.

Read one JSON batch on stdin, return one JSON array. No CSV/DB/network access.
For ordinary sample sizes use direct multinomial powers, not JS's log algorithm.
The common multinomial coefficient cancels when normalizing across settings.
"""
from decimal import Decimal, localcontext
import json
import sys


def posterior(case):
    with localcontext() as ctx:
        ctx.prec = 80
        n, bb, rb = (int(case[key]) for key in ('games', 'bb', 'rb'))
        other = n - bb - rb
        weights = [Decimal(str(value)) for value in case.get('priors', [1] * 6)]
        probabilities = []
        for row in case['settings']:
            p_bb = Decimal(1) / Decimal(str(row['bbDenominator']))
            p_rb = Decimal(1) / Decimal(str(row['rbDenominator']))
            probabilities.append((p_bb, p_rb, Decimal(1) - p_bb - p_rb))
        if n <= 100_000:
            scores = [weight * a ** bb * b ** rb * c ** other
                      for weight, (a, b, c) in zip(weights, probabilities)]
        else:
            logs = [(weight.ln() + bb * a.ln() + rb * b.ln() + other * c.ln())
                    if weight else None
                    for weight, (a, b, c) in zip(weights, probabilities)]
            maximum = max(value for value in logs if value is not None)
            scores = [Decimal(0) if value is None or value - maximum < -1000
                      else (value - maximum).exp() for value in logs]
        total = sum(scores)
        return [float(value / total) for value in scores]


if __name__ == '__main__':
    cases = json.loads(sys.stdin.buffer.read().decode('utf-8'))
    print(json.dumps([posterior(case) for case in cases]))
