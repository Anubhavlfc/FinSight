# Company-specific mappings

Put overrides for one filer in `config/companies/<10-digit CIK>.toml`. Tags listed
here are tried **before** the defaults in `config/mappings.toml` for that company only.

```toml
# config/companies/0000000000.toml
[fields.revenue]
tags = ["us-gaap:Revenues"]          # prepended to the default priority list

[fields.capital_expenditure]
tags = ["us-gaap:PaymentsToAcquireProductiveAssets"]
```

Only `tags` can be overridden per company. Period type, sign handling and the
metrics a field feeds stay global so every company is calculated the same way.
Add an override when a validation run reports a missing field or a comparability
warning and you have confirmed in the filing which tag the company uses.
