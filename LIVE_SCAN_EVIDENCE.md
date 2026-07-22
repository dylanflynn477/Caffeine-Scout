# Live scan evidence

This is a dated verification record, not a promise that retailer prices remain current.

## Successful live retrieval

- Run date: July 22, 2026
- Location configuration: ZIP 19103, five-mile maximum distance
- Public source: Target
- Authentication, CAPTCHA, proxy, and anti-bot bypasses: none
- Robots result: allowed
- HTTP result: 200
- Extraction method: anonymous Playwright-rendered public DOM

Diagnostic command:

```powershell
caffeine-scout diagnose-source target --config config.example.yaml
```

The configured Monster brand page yielded 17 raw public offers and the general energy
category yielded 10. The subsequent persisted Monster scan was:

```powershell
caffeine-scout scan --brand Monster --format csv --config config.example.yaml
```

SQLite scan run 7 stored 16 normalized offers: 15 live Target offers and one clearly
labeled offline MockMart comparison. CVS returned HTTP 403 and was recorded as a source
failure without stopping Target or MockSource.

## Best live result in scan run 7

Three Target 12-packs tied at:

- Effective price: $25.49
- Pack: 12 cans × 16 fl oz
- Price per can: $2.1242
- Robbery Index: 14, Market Price
- Collected: July 22, 2026 at approximately 6:08 PM EDT

Products:

- [Monster Energy Original 12-pack](https://www.target.com/p/monster-energy-original-12pk-16-fl-oz-cans/-/A-81782413)
- [Monster Ultra variety 12-pack](https://www.target.com/p/monster-ultra-variety-pack-including-zero-ultra-peachy-keen-strawberry-dreams-energy-drink-12pk-16-fl-oz-cans/-/A-90920426)
- [Monster Ultra VP 12-pack](https://www.target.com/p/monster-energy-ultra-vp-zero-ultra-ultra-blue-hawaiian-ultra-punk-punch-energy-drink-12pk-16-fl-oz/-/A-95017557)

The $22.99 / $1.9158 MockMart result was not presented as a live retailer result.

## Limitations observed

Target did not expose fulfillment or confirmed 19103 store inventory in these anonymous
cards, so the stored fulfillment and availability values remain `unknown`. A later
all-brand attempt received no product DOM from Target and recorded
`no_public_price_data_found`; this demonstrates that live retailer rendering can be
transient. CVS returned HTTP 403 and the adapter stopped without attempting a bypass.

Use the product URLs and collection timestamps to recheck a price before purchasing.
