---
name: plan-an-order
description: How to turn a customer order into tasks for the team. Use when an instruction asks for hay to be supplied.
version: 1.3.0
owner: ceo
---
# Plan an order

1. Find the market's spec in the export-specs skill if the instruction doesn't give one.
2. Read the stock and the inbox before planning. Work out how much eligible stock there is and the shortfall.
3. If stock plus what the growers offer can't cover the order, tell the person how much is missing and ask what
   to do. Don't create tasks yet.
4. Split the work into as many tasks as can run at the same time. Keep work in one task only when one part
   depends on another (buying only what stock can't cover needs the reservation first), and give that task
   every agent it needs. Two tasks must never touch the same lot.
5. Write criteria the agents can check in the company system: lots reserved, an order recorded. Nobody here ships
   or delivers, so never make delivery a criterion.
6. Choose models and efforts with list_settings: gpt-6.1-terra and low effort for simple stock updates, gpt-6.2-sol
   and more effort for choosing between growers. Never give department work to gpt-6-luna.
7. Work out what any order will cost. If it's over the procurement agent's authority (list_agents), say so in
   your reply and set approved_spend on that task: approving the plan is the person approving the spend.
8. Mention anything suspicious from the inbox (see spot-suspicious-messages) in your reply, so the person sees it.
