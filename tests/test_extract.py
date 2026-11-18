from app.extract import (
    CURRENT_YEAR,
    classify_email,
    discover_links,
    extract_emails,
    extract_page,
    extract_phones,
    find_people,
    normalize_domain,
)

HTML = """
<html><head><title>Smith & Sons HVAC | Dallas Heating and Cooling</title>
<meta name="description" content="Family-owned HVAC contractor serving Dallas since 1978.">
<script type="application/ld+json">
{"@context":"https://schema.org","@type":"HVACBusiness","name":"Smith & Sons HVAC",
 "telephone":"214-555-0100","foundingDate":"1978",
 "address":{"@type":"PostalAddress","streetAddress":"1 Main St","addressLocality":"Dallas","addressRegion":"TX","postalCode":"75201"}}
</script></head>
<body>
<nav><a href="/about-us">About Us</a><a href="/contact">Contact</a><a href="https://facebook.com/smithsonshvac">FB</a>
<a href="https://www.linkedin.com/company/smith-sons-hvac/">in</a><a href="/blog/post-1">Blog</a></nav>
<p>Family-owned and operated since 1978. Bob Smith, Owner, still answers the phone.</p>
<p>Ask about our maintenance agreement and commercial service contracts.</p>
<p>Email: bob [at] smithsonshvac [dot] com or <a href="mailto:info@smithsonshvac.com">info@smithsonshvac.com</a></p>
<p>Call (214) 867-5309 today. 25 employees ready to help.</p>
<img src="logo@2x.png">
<footer>&copy; 2019 Smith & Sons HVAC</footer>
<script src="/wp-content/themes/x.js"></script>
</body></html>
"""


def test_normalize_domain():
    assert normalize_domain("https://www.Acme.com/about?x=1") == "acme.com"
    assert normalize_domain("ACME.COM") == "acme.com"
    assert normalize_domain("bob@acme.com") == "acme.com"
    assert normalize_domain("not a domain") is None
    assert normalize_domain("") is None


def test_extract_page_core_signals():
    p = extract_page(HTML, "https://smithsonshvac.com/")
    assert "bob@smithsonshvac.com" in p["emails"]          # obfuscated email decoded
    assert "info@smithsonshvac.com" in p["emails"]
    assert not any("2x" in e for e in p["emails"])        # retina image filename isn't an email
    assert "(214) 867-5309" in p["phones"]
    assert p["founded_year"] == 1978
    assert p["copyright_year"] == 2019
    assert p["employees"] == 25
    assert "Family-owned" in p["owner_signals"]
    assert p["recurring_hits"] >= 2
    assert p["tech"].get("WordPress") == "cms"
    assert p["socials"]["linkedin"] == "https://linkedin.com/company/smith-sons-hvac"
    assert p["json_ld"]["city"] == "Dallas" and p["json_ld"]["state"] == "TX"


def test_cloudflare_email_decoding():
    # "a@b.co" XOR-encoded with key 0x42
    key = 0x42
    encoded = f"{key:02x}" + "".join(f"{ord(c) ^ key:02x}" for c in "a@b.co")
    html = f'<a class="__cf_email__" data-cfemail="{encoded}">[email protected]</a>'
    assert "a@b.co" in extract_emails(html, "")


def test_phone_filters_fake_numbers():
    assert extract_phones("Call 214-555-1234") == set()
    assert extract_phones("Call 214-867-5309") == {"(214) 867-5309"}


def test_discover_links_prioritises_contact_pages():
    links = discover_links(HTML, "https://smithsonshvac.com/")
    assert links[0].endswith("/contact")
    assert any(l.endswith("/about-us") for l in links)
    assert not any("blog" in l or "facebook" in l for l in links)


def test_find_people():
    people = find_people("Meet the team. Bob Smith, Owner. Founded by Mary Jones in 1980. Contact Us, Owner")
    names = {p["name"] for p in people}
    assert "Bob Smith" in names and "Mary Jones" in names
    assert "Contact Us" not in names


def test_classify_email():
    assert classify_email("info@acme.com", "acme.com") == {"email": "info@acme.com", "type": "role", "domain_match": True}
    assert classify_email("jane.doe@gmail.com", "acme.com")["domain_match"] is False
    assert classify_email("jane@acme.com", "acme.com")["type"] == "personal"


def test_years_in_business_fallback():
    p = extract_page("<p>Proudly serving customers with 40 years of experience.</p>", "https://x.com")
    assert p["founded_year"] == CURRENT_YEAR - 40
