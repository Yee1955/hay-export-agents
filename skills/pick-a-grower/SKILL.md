---
name: pick-a-grower
description: How to choose one grower's quote to cover a hay shortfall.
version: 1.3.0
owner: procurement_manager
---
# Pick a grower to cover a shortfall

1. Only quotes that meet the market's spec count (see export-specs).
2. One grower covers the whole shortfall. Don't split the order.
3. If no single grower can cover it, place no order: set the task blocked and say how many tonnes are missing.
4. If the price difference is under 8%, pick the grower with the better on-time rate: a late load misses the ship.
   Otherwise pick the cheaper one.
5. Record the order with write_stock: a new lot id like HART-120, the tonnes, the quoted moisture, status
   ordered, the grower and price in the note, price_per_tonne and task_id. If it's refused as over your authority
   limit, set the task blocked and say what needs a person's approval.
6. Give a one-line reason with the price and on-time rate of the grower you chose and the one you passed over.
7. Check the inbox with spot-suspicious-messages and report anything odd for a person to check.
