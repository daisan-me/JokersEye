'use strict';

/* Function definitions only. Creating a registry binds model references; it
   never evaluates observations. The UI deliberately has no execution path. */
((root) => {
  function numeric(value, label, {integer = false, minimum = -Infinity} = {}) {
    if ((typeof value !== 'number' && typeof value !== 'string')
        || (typeof value === 'string' && !/^[+-]?(?:\d+(?:\.\d*)?|\.\d+)(?:e[+-]?\d+)?$/i.test(value.trim()))) {
      throw new TypeError(`${label}を指定してください。`);
    }
    const number = Number(value);
    if (!Number.isFinite(number) || number < minimum || (integer && !Number.isSafeInteger(number))) {
      throw new RangeError(`${label}の値が不正です。`);
    }
    return number;
  }

  function record(value, label) {
    if (!value || typeof value !== 'object' || Array.isArray(value)) {
      throw new TypeError(`${label}を指定してください。`);
    }
    return value;
  }

  function reference(value) {
    record(value, '機種');
    if (typeof value.id !== 'string' || !value.id.trim()) throw new TypeError('機種IDが不正です。');
    return value;
  }

  function denseArray(value, length, label) {
    if (!Array.isArray(value) || (length !== null && value.length !== length)
        || Array.from({length: value.length}, (_, i) => Object.hasOwn(value, i)).some(present => !present)) {
      throw new RangeError(`${label}を欠けなく指定してください。`);
    }
    return value;
  }

  function observations(input) {
    record(input, '観測値');
    const games = numeric(input.games, '通常時ゲーム数', {integer: true, minimum: 1});
    const bb = numeric(input.bb, 'BB回数', {integer: true, minimum: 0});
    const rb = numeric(input.rb, 'RB回数', {integer: true, minimum: 0});
    if (bb > games || rb > games - bb) throw new RangeError('BB・RB回数の合計がゲーム数を超えています。');
    return {games, bb, rb};
  }

  // log(a / b), without overflowing the ratio or losing nearly equal values.
  function logRatio(a, b) {
    const relative = (a - b) / b;
    return Math.abs(relative) < 0.5 ? Math.log1p(relative) : Math.log(a) - Math.log(b);
  }

  function estimateSettings(machine, input, options = {}) {
    reference(machine);
    record(options, '計算条件');
    const {games, bb, rb} = observations(input);
    const specs = options.settingSpecs ?? machine.settings;
    denseArray(specs, 6, '設定1〜6の理論値');
    if (specs.some((row, i) => !row || typeof row !== 'object' || row.setting !== i + 1)) {
      throw new RangeError('設定1〜6の理論値を順番に指定してください。');
    }
    const priors = options.priors ?? [1, 1, 1, 1, 1, 1];
    denseArray(priors, 6, '設定1〜6の事前の重み');
    const weights = priors.map(value => numeric(value, '事前の重み', {minimum: 0}));
    if (!weights.some(value => value > 0)) throw new RangeError('事前の重みに正の値が必要です。');
    const rates = specs.map(row => {
      const bbDenominator = numeric(row.bbDenominator, 'BB理論確率の分母', {minimum: 1});
      const rbDenominator = numeric(row.rbDenominator, 'RB理論確率の分母', {minimum: 1});
      const pBB = 1 / bbDenominator;
      const pRB = 1 / rbDenominator;
      const bonusProbability = pBB + pRB;
      if (bonusProbability >= 1) throw new RangeError('BB・RBの理論確率が不正です。');
      return {bbDenominator, rbDenominator, bonusProbability};
    });
    // Compute likelihood RATIOS, not enormous absolute log likelihoods. Then
    // re-anchor at the winning setting so small prior differences survive even
    // for identical rates at maximum-safe-integer sample sizes.
    const relativeScores = anchor => rates.map((rate, index) => {
      if (weights[index] === 0) return -Infinity;
      const baseline = rates[anchor];
      const otherRatio = (baseline.bonusProbability - rate.bonusProbability) / (1 - baseline.bonusProbability);
      const logOther = Math.abs(otherRatio) < 0.5 ? Math.log1p(otherRatio)
        : Math.log1p(-rate.bonusProbability) - Math.log1p(-baseline.bonusProbability);
      return logRatio(weights[index], weights[anchor])
        + bb * logRatio(baseline.bbDenominator, rate.bbDenominator)
        + rb * logRatio(baseline.rbDenominator, rate.rbDenominator)
        + (games - bb - rb) * logOther;
    });
    const initialScores = relativeScores(weights.findIndex(value => value > 0));
    const anchor = initialScores.indexOf(Math.max(...initialScores));
    const logWeights = relativeScores(anchor);
    const largest = Math.max(...logWeights);
    const likelihoods = logWeights.map(value => Math.exp(value - largest));
    const total = likelihoods.reduce((sum, value) => sum + value, 0);
    return {
      modelId: machine.id, method: 'bb-rb-multinomial-bayes',
      settings: specs.map((row, index) => ({setting: row.setting,
        probability: likelihoods[index] / total, percent: likelihoods[index] / total * 100})),
      evidence: {games, bb, rb}, priorWeights: weights,
      // Payout and inferred grapes are not additional independent evidence.
      sourceUrl: machine.sourceUrl,
    };
  }

  function reverseGrapes(machine, input, options = {}) {
    reference(machine);
    record(options, '計算条件');
    const {games, bb, rb} = observations(input);
    const net = numeric(input.net, '差枚', {integer: true});
    const bbNet = numeric(options.bbNetCoins ?? machine.bonusNetCoins?.bb, 'BB1回の純獲得枚数', {minimum: 0});
    const rbNet = numeric(options.rbNetCoins ?? machine.bonusNetCoins?.rb, 'RB1回の純獲得枚数', {minimum: 0});
    const grapeAward = numeric(options.grapePayoutCoins ?? machine.grapePayoutCoins, 'ぶどう1回の払い出し枚数', {minimum: Number.MIN_VALUE});
    const bet = numeric(options.betCoins ?? 3, '通常時の掛け枚数', {minimum: Number.MIN_VALUE});
    const hasReplayCount = input.replayCount !== null && input.replayCount !== undefined && input.replayCount !== '';
    const hasReplayRate = input.replayDenominator !== null && input.replayDenominator !== undefined && input.replayDenominator !== '';
    if (hasReplayCount === hasReplayRate) throw new TypeError('リプレイは回数または確率の分母を一つ指定してください。');
    const replayCount = hasReplayCount
      ? numeric(input.replayCount, 'リプレイ回数', {integer: true, minimum: 0})
      : games / numeric(input.replayDenominator, 'リプレイ確率の分母', {minimum: 1});
    const hasCherryCoins = input.cherryCoins !== null && input.cherryCoins !== undefined && input.cherryCoins !== '';
    const hasCherryRate = input.cherryCoinsPerGame !== null && input.cherryCoinsPerGame !== undefined && input.cherryCoinsPerGame !== '';
    if (hasCherryCoins === hasCherryRate) throw new TypeError('チェリーは獲得枚数の合計または1Gあたりの見込み枚数を一つ指定してください。');
    const cherryCoins = hasCherryCoins
      ? numeric(input.cherryCoins, 'チェリー獲得枚数の合計', {minimum: 0})
      : games * numeric(input.cherryCoinsPerGame, 'チェリー1Gあたりの見込み枚数', {minimum: 0});
    const otherPayoutCoins = numeric(input.otherPayoutCoins ?? 0, 'その他小役の獲得枚数', {minimum: 0});
    if (replayCount > games - bb - rb) throw new RangeError('リプレイ回数が通常時の非ボーナスゲーム数を超えています。');
    // Normal-game cash-flow identity. Bonus awards are NET gains, and replay
    // contributes the saved bet. Do not count bonus games in `games`.
    const terms = [games * bet, net, -bb * bbNet, -rb * rbNet,
      -replayCount * bet, -cherryCoins, -otherPayoutCoins];
    if (terms.some(value => !Number.isFinite(value))) throw new RangeError('計算条件が数値範囲を超えています。');
    // Neumaier compensated summation preserves small residual coin balances.
    let sum = 0, correction = 0;
    for (const term of terms) {
      const next = sum + term;
      correction += Math.abs(sum) >= Math.abs(term) ? (sum - next) + term : (term - next) + sum;
      sum = next;
    }
    const grapeCoins = sum + correction;
    let estimatedCount = grapeCoins / grapeAward;
    const availableGames = games - bb - rb - replayCount;
    const tolerance = 8 * Number.EPSILON * Math.max(1, ...terms.map(Math.abs)) / grapeAward;
    if (!Number.isFinite(estimatedCount) || (grapeCoins > 0 && estimatedCount === 0) || !Number.isFinite(tolerance)
        || tolerance > Math.max(1, games) * 1e-9) {
      throw new RangeError('計算条件が数値範囲または必要な精度を超えています。');
    }
    if (estimatedCount < -tolerance || estimatedCount > availableGames + tolerance) {
      throw new RangeError('逆算値が成立しません。差枚、獲得枚数、ゲーム数の数え方を確認してください。');
    }
    // Only floating-point noise outside a valid boundary is clamped. Positive
    // fractional estimates are intentionally never rounded to integer counts.
    estimatedCount = Math.max(0, Math.min(availableGames, estimatedCount));
    const denominator = estimatedCount === 0 ? null : games / estimatedCount;
    if (denominator !== null && !Number.isFinite(denominator)) throw new RangeError('ぶどう確率が数値範囲を超えています。');
    return {modelId: machine.id, estimatedCount,
      probability: estimatedCount / games,
      denominator,
      assumptions: {games, bb, rb, net, bbNetCoins: bbNet, rbNetCoins: rbNet,
        grapePayoutCoins: grapeAward, betCoins: bet, replayCount, cherryCoins, otherPayoutCoins},
      approximate: true, sourceUrl: machine.sourceUrl};
  }

  function freeze(value) {
    if (value && typeof value === 'object') {
      Object.values(value).forEach(freeze);
      Object.freeze(value);
    }
    return value;
  }

  function createCalculators(catalog) {
    record(catalog, '機種一覧');
    denseArray(catalog.machines, null, '機種一覧');
    const byModel = Object.create(null);
    catalog.machines.forEach(item => {
      reference(item);
      if (Object.hasOwn(byModel, item.id)) throw new Error('機種IDが重複しています。');
      const machine = freeze(JSON.parse(JSON.stringify(item)));
      byModel[machine.id] = Object.freeze({
        reference: machine,
        reverseGrapes: (input, options) => reverseGrapes(machine, input, options),
        estimateSettings: (input, options) => estimateSettings(machine, input, options),
      });
    });
    return Object.freeze(byModel);
  }

  root.JokersJugglerMath = Object.freeze({createCalculators, estimateSettings, reverseGrapes});
})(globalThis);
