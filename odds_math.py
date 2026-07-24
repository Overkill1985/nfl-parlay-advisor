"""Pure odds/EV math: American<->decimal<->implied-probability conversions,
parlay combination, expected value, and a risk-score heuristic.

No I/O, no ESPN/storage dependencies - used both by the model-generated
parlay flow (parlay_engine.py, "fair odds" from a probability estimate) and
the tracked-picks flow (real sportsbook odds entered by hand).
"""


def american_to_decimal(odds):
    if odds is None:
        return None
    if odds > 0:
        return 1 + odds / 100
    return 1 + 100 / abs(odds)


def decimal_to_american(decimal_odds):
    if decimal_odds is None or decimal_odds <= 1:
        return None
    if decimal_odds >= 2:
        return round((decimal_odds - 1) * 100)
    return round(-100 / (decimal_odds - 1))


def implied_probability(odds):
    """Sportsbook-implied win probability from American odds. Includes the
    book's vig - this is not a "true" probability, just what the price says."""
    if odds is None:
        return None
    if odds > 0:
        return 100 / (odds + 100)
    return abs(odds) / (abs(odds) + 100)


def fair_decimal_odds(probability):
    """Vig-free decimal odds implied by a probability estimate (1/p)."""
    if not probability or probability <= 0:
        return None
    return 1 / probability


def combined_decimal_odds(american_odds_list):
    """Standard parlay math: multiply each leg's decimal odds together."""
    result = 1.0
    for odds in american_odds_list:
        decimal = american_to_decimal(odds)
        if decimal is None:
            continue
        result *= decimal
    return result


def payout(stake, decimal_odds):
    """Total return (stake back + profit) if the bet hits."""
    return stake * decimal_odds


def profit(stake, decimal_odds):
    return stake * (decimal_odds - 1)


def expected_value(probability, decimal_odds, stake):
    """EV in currency units: probability-weighted profit minus the
    complementary-probability-weighted loss of the stake."""
    if probability is None or decimal_odds is None or stake is None:
        return None
    win_profit = profit(stake, decimal_odds)
    return probability * win_profit - (1 - probability) * stake


def risk_score(leg_probabilities):
    """0-100 heuristic risk score for a set of leg hit-probabilities. Higher
    = riskier. Blends leg count, combined probability, and the weakest
    individual leg (one shaky leg can sink an otherwise strong ticket)."""
    if not leg_probabilities:
        return 0

    num_legs = len(leg_probabilities)
    combined = 1.0
    for p in leg_probabilities:
        combined *= p
    min_leg_prob = min(leg_probabilities)

    leg_component = min(num_legs, 8) / 8 * 40
    probability_component = (1 - combined) * 40
    weak_leg_component = (1 - min_leg_prob) * 20

    return round(min(100, max(0, leg_component + probability_component + weak_leg_component)))
