import router
import actions

print("=== ROUTER TEST ===")
cases = [
    "yt pe harsh tiwari search karo",
    "youtube pe music search karo",
    "chrome kholo",
    "food4all ka doc kholo",
    "google karo best laptop",
]

for text in cases:
    i = router.route(text)
    print(f"  {text!r} -> {i.name} / term={i.term!r}")

print("\n=== DRY-RUN TEST (preview) ===")
from main import DRY_RUN, handle
import actions

# Test preview without actually running
test_cases = [
    "yt pe harsh tiwari search karo",
    "food4all ka doc kholo",
    "google karo best laptop",
]

for text in test_cases:
    print(f"\n  Testing: {text!r}")
    i = router.route(text)
    result = actions.preview(i, text)
    print(f"  preview: {result.say}")