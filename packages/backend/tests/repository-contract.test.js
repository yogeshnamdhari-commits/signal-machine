const fs = require('fs');
const path = require('path');

describe('backend repository contracts', () => {
  const index = fs.readFileSync(path.join(__dirname, '..', 'src', 'index.ts'), 'utf8');
  const routes = fs.readFileSync(path.join(__dirname, '..', 'src', 'routes', 'index.ts'), 'utf8');

  test('Python is the only executable trading authority', () => {
    expect(index).toContain("PYTHON_CANONICAL_AUTHORITY");
    expect(index).not.toContain('signalEngine.startContinuousScan');
    expect(index).not.toContain('tradeSimulator.start()');
  });

  test('Node mutation endpoints are explicitly blocked', () => {
    expect(index).toContain("/indicators/signal");
    expect(index).toContain("/risk/position/size");
    expect(index).toContain("/scanner/scan");
    expect(routes).toContain('PYTHON_CANONICAL_AUTHORITY');
  });

  test('orderflow never fabricates 50/50 flow', () => {
    expect(routes).not.toContain('takerBuyVol: vol * 0.5');
    expect(routes).not.toContain('takerSellVol: vol * 0.5');
    expect(routes).not.toContain('buyRatio: 0.5');
    expect(routes).toContain("dataQuality: 'UNAVAILABLE'");
  });
});
