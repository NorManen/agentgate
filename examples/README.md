# Toy example

`toy_project/` is a tiny project with one bug: `apply_discount` returns the amount
taken off instead of the discounted price, so three of its four tests fail.
That is intentional; it is the work the demo hands to the workers.

`task.json` is the scoped task for it: the worker may change `discount.py` only,
must not touch `test_discount.py`, and is done when the tests pass.

Run the demo from the repository root:

    python -m agentgate demo
