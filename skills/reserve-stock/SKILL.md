---
name: reserve-stock
description: How to reserve hay lots for a customer order without taking stock promised elsewhere.
version: 1.0.0
owner: production_manager
---
# Reserve stock for an order

1. A lot is eligible if its status is in_stock and it meets the market's spec (see export-specs).
2. Never reserve a lot that is already reserved or ordered for someone else.
3. Reserve whole lots. Don't split a lot between orders.
4. Reserve with write_stock: keep the lot's tonnes and moisture, set status to reserved, and put the customer's
   name in the note.
5. Record the lots and total tonnes you reserved, and any lot you left out and why, with update_task.
6. If eligible stock is more than the order needs, reserve the smallest set of lots that covers it.
