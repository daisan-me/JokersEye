'use strict';

// Pure batch adapter. The tested mathematics remains in juggler-math.js.
((root) => {
  const normalize = name => String(name ?? '').normalize('NFKC').replace(/[\s・]/g, '').toUpperCase();
  const aliases = {
    'SアイムジャグラーEX': 'imjugglerex2020',
    'ファンキージャグラー2KT': 'funkyjuggler2',
    'ファンキージャグラー2NT': 'funkyjuggler2',
    'ハッピージャグラーVIII': 'happyjugglerv3',
    'ハッピージャグラーV3': 'happyjugglerv3',
  };
  function matchModel(name, catalog) {
    const key = normalize(name);
    // This unsuffixed report label also occurs during the SS era. Do not
    // silently substitute the old 5th-generation Girls or the SS model.
    if (key === 'ジャグラーガールズ') return null;
    if (aliases[key]) return aliases[key];
    const matches = catalog.machines.filter(machine => normalize(machine.name) === key);
    return matches.length === 1 ? matches[0].id : null;
  }

  function createProcessor(catalog, config) {
    const calculators = root.JokersJugglerMath.createCalculators(catalog);
    const settings = config.models;
    return row => {
      const output = {row_index: row.row_index, setting_status: 'pending', grape_status: 'pending',
        setting_error: '', grape_error: '', setting_expected: null, setting_map: null,
        setting_mode_count: null, setting_max_p: null, setting_p4plus: null, setting_p5plus: null,
        grape_count: null, grape_probability: null, grape_denominator: null,
        replay_expected_count: null, cherry_expected_coins: null, other_expected_coins: null};
      for (let i = 1; i <= 6; i++) output['setting_p' + i] = null;
      const unavailable = (code, message) => {
        output.setting_status = output.grape_status = code;
        output.setting_error = output.grape_error = message;
        return output;
      };
      if (row.games === null) return unavailable('missing_games', 'ゲーム数が未取得です。');
      if (row.games === 0) return unavailable('zero_games', '0Gのため設定推測・ぶどう確率を計算できません。');
      if (row.bb === null || row.rb === null) return unavailable('missing_bonus',
        `${row.bb === null ? 'BB' : ''}${row.bb === null && row.rb === null ? '・' : ''}${row.rb === null ? 'RB' : ''}が未取得です。`);
      if (![row.games, row.bb, row.rb].every(Number.isSafeInteger) || row.games < 0 || row.bb < 0 || row.rb < 0
          || row.bb > row.games || row.rb > row.games - row.bb) return unavailable('invalid_observations', 'G数・BB・RBの整合性がありません。');
      const conditions = settings[row.model];
      const calculator = conditions?.modelId ? calculators[conditions.modelId] : null;
      if (!calculator) return unavailable('unmapped_model', '機種名と公式理論値の対応が未指定です。');
      try {
        const result = calculator.estimateSettings(row, {priors: config.priors});
        result.settings.forEach(item => { output['setting_p' + item.setting] = item.probability; });
        output.setting_expected = result.settings.reduce((sum, item) => sum + item.setting * item.probability, 0);
        output.setting_max_p = Math.max(...result.settings.map(item => item.probability));
        const modes = result.settings.filter(item => Math.abs(item.probability - output.setting_max_p) <= 1e-14);
        output.setting_mode_count = modes.length;
        output.setting_map = modes.length === 1 ? modes[0].setting : null;
        output.setting_p4plus = result.settings.slice(3).reduce((sum, item) => sum + item.probability, 0);
        output.setting_p5plus = result.settings.slice(4).reduce((sum, item) => sum + item.probability, 0);
        output.setting_status = 'ok';
      } catch (error) {
        output.setting_status = calculator.reference.settings.some(item => item.bbDenominator === null || item.rbDenominator === null)
          ? 'spec_missing' : 'numeric_error';
        output.setting_error = error.message;
      }
      if (row.net === null) {
        output.grape_status = 'missing_net'; output.grape_error = '差枚が未取得です。'; return output;
      }
      if (conditions.replayDenominator === null || conditions.cherryCoinsPerGame === null
          || conditions.bbNetCoins === null || conditions.rbNetCoins === null || conditions.grapePayoutCoins === null) {
        output.grape_status = 'missing_grape_params';
        output.grape_error = 'リプレイ・チェリーの見込み条件、または獲得枚数を指定してください。'; return output;
      }
      try {
        const result = calculator.reverseGrapes({...row,
          replayDenominator: conditions.replayDenominator, cherryCoinsPerGame: conditions.cherryCoinsPerGame,
          otherPayoutCoins: row.games * conditions.otherCoinsPerGame},
        {bbNetCoins: conditions.bbNetCoins, rbNetCoins: conditions.rbNetCoins,
          grapePayoutCoins: conditions.grapePayoutCoins, betCoins: config.betCoins});
        output.grape_count = result.estimatedCount;
        output.grape_probability = result.probability;
        output.grape_denominator = result.denominator;
        output.replay_expected_count = result.assumptions.replayCount;
        output.cherry_expected_coins = result.assumptions.cherryCoins;
        output.other_expected_coins = result.assumptions.otherPayoutCoins;
        output.grape_status = 'ok';
      } catch (error) {
        output.grape_status = 'cashflow_invalid'; output.grape_error = error.message;
      }
      return output;
    };
  }
  root.JokersJugglerBatch = Object.freeze({normalize, matchModel, createProcessor});
})(globalThis);
