import html
from datetime import datetime
from zoneinfo import ZoneInfo
import json
import os
import re
import sys
import urllib.parse
import urllib.request

OUTLET_URL = "https://www.veloretti.com/nl/outlet"
CUSTOMIZER_URL = "https://www.veloretti.com/nl/electric-bikes/ace-two/customizer"
STATE_PATH = "state/prices.json"
PRODUCT_RE = re.compile(r"ace\s+two\s+(pro|lite)", re.IGNORECASE)

def fetch(url):
    req = urllib.request.Request(url, headers={"User-Agent": "Mozilla/5.0 (compatible; VelorettiOutletChecker/1.0)"})
    with urllib.request.urlopen(req, timeout=30) as response:
        return response.read().decode("utf-8", errors="replace")

def visible_text(source):
    source = html.unescape(source)
    source = re.sub(r"<script\b[^>]*>.*?</script>", " ", source, flags=re.I | re.S)
    source = re.sub(r"<style\b[^>]*>.*?</style>", " ", source, flags=re.I | re.S)
    return re.sub(r"\s+", " ", re.sub(r"<[^>]+>", " ", source)).strip()

try:
    outlet_html = fetch(OUTLET_URL)
    customizer_html = fetch(CUSTOMIZER_URL)
except Exception as exc:
    sys.exit(f"Could not fetch Veloretti pages: {exc}")

# Match actual product-card links and names on the outlet page.
outlet_products = {}
for match in re.finditer(r"""<a\b[^>]*href=["']([^"']+)["'][^>]*>(.*?)</a>""", html.unescape(outlet_html), re.I | re.S):
    href, content = match.groups()
    title = re.sub(r"\s+", " ", re.sub(r"<[^>]+>", " ", html.unescape(content))).strip()
    model_match = PRODUCT_RE.search(title)
    if model_match:
        url = urllib.parse.urljoin(OUTLET_URL, href)
        if urllib.parse.urlparse(url).hostname in ("www.veloretti.com", "veloretti.com"):
            outlet_products[model_match.group(1).lower()] = {"title": title, "url": url}

# Read the static page text in ordered label/price pairs; fail closed if the page changes.
text = visible_text(customizer_html)
matches = list(re.finditer(r"\b(Lite|Pro)\b", text, re.I))
prices = {}
for index, label in enumerate(matches):
    model = label.group(1).lower()
    if model in prices:
        continue
    end = matches[index + 1].start() if index + 1 < len(matches) else min(len(text), label.end() + 500)
    window = text[label.end():end]
    price = re.search(r"€\s*[0-9][0-9.]*,[0-9]{2}", window)
    if not price:
        # Lite's price is displayed before its label in some customizer layouts.
        if model == "lite":
            before = re.findall(r"€\s*[0-9][0-9.]*,[0-9]{2}", text[max(0, label.start()-120):label.start()])
            if before:
                prices[model] = re.sub(r"\s+", "", before[-1])
                continue
        sys.exit(f"Could not extract {model.title()} price from customizer.")
    prices[model] = re.sub(r"\s+", "", price.group(0))

if set(prices) != {"lite", "pro"}:
    sys.exit(f"Expected Lite and Pro prices; extracted keys: {sorted(prices)}")

try:
    with open(STATE_PATH, encoding="utf-8") as file:
        old_state = json.load(file)
except FileNotFoundError:
    old_state = {}

old_prices = old_state.get("customizer_prices", {})
price_changes = [(m, old_prices[m], prices[m]) for m in ("lite", "pro")
                 if m in old_prices and old_prices[m] != prices[m]]
old_outlet = old_state.get("outlet_models", {})
newly_listed = [m for m in outlet_products if m not in old_outlet]

notifications = []
for model in newly_listed:
    product = outlet_products[model]
    notifications.append(("Hij is nu beschikbaar!",
        f"{product['title']} is nu beschikbaar in de Veloretti-outlet.\nKoop hem hier: {product['url']}"))
for model, before, now in price_changes:
    notifications.append(("Let op, prijsverandering",
        f"Ace Two {model.title()}\nOude prijs: {before}\nNieuwe prijs: {now}\nBekijk de pagina: {CUSTOMIZER_URL}"))
# Send a daily current-price digest during the noon run (Amsterdam time).
if datetime.now(ZoneInfo("Europe/Amsterdam")).hour == 12:
    notifications.append((
        "Veloretti Ace Two - dagelijkse prijsupdate",
        f"🚲 Ace Two Lite: {prices['lite']} | Ace Two Pro: {prices['pro']}. Bekijk de uitvoeringen: {CUSTOMIZER_URL}"
    ))


if notifications:
    topic = os.environ.get("NTFY_TOPIC", "").strip()
    parsed = urllib.parse.urlparse(topic if "://" in topic else "https://ntfy.sh/" + topic)
    if parsed.scheme != "https" or not parsed.netloc or not parsed.path.strip("/"):
        sys.exit("NTFY_TOPIC must be an https ntfy topic URL or topic name.")
    target = f"https://{parsed.netloc}/{parsed.path.strip('/')}"
    for title, message in notifications:
        req = urllib.request.Request(target, data=message.encode(),
            headers={"Title": title, "Priority": "high", "Tags": "bike"}, method="POST")
        with urllib.request.urlopen(req, timeout=20) as response:
            print(f"Sent {title} (HTTP {response.status}).")
else:
    print("No new outlet listing or price change.")

os.makedirs("state", exist_ok=True)
with open(STATE_PATH, "w", encoding="utf-8") as file:
    json.dump({"customizer_prices": prices, "outlet_models": outlet_products},
              file, ensure_ascii=False, indent=2)
print(f"Saved state: Lite={prices['lite']}, Pro={prices['pro']}")
