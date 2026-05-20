- [x] Inspect existing backend scheduling logic and API response shape.
- [ ] Add DB fields (timestamps) for accepted/cooking/packed/served/completed.
- [ ] Add background automation worker to advance orders every 20 seconds through stages.
- [ ] Update order_items states automatically (queued -> cooking -> done).
- [ ] Set order.status automatically (pending -> accepted -> packing -> completed).
- [ ] On completion, add completed orders payload for history (dashData.completed).
- [ ] Add dashData.agent_log payload and generate risk/critical-thinking logs.
- [ ] Update at_risk flag in real time (compare estimated completion vs deadline).
- [ ] Run server and do manual verification by placing an order; confirm UI updates.


