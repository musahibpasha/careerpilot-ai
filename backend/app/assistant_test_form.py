"""A local, fake job-application form for testing browser assistance safely.

Served by this app at /assistant-test-form. Nothing here talks to a real
employer. Submitting it only increments a counter, so tests (and you) can
prove that the assistant never submitted anything.

Variants (query string):  ?variant=captcha | login | empty
"""

SUBMISSIONS = {"count": 0}

_STYLE = """
body{font-family:system-ui,sans-serif;max-width:640px;margin:2rem auto;padding:0 1rem}
label{display:block;margin:.9rem 0 .2rem;font-weight:600}
input,textarea,select{width:100%;padding:.5rem;box-sizing:border-box}
.note{background:#eef;padding:.6rem;border-radius:6px}
"""

_FORM = """
<form id="application" method="post" action="/assistant-test-form/submit">
  <div class="field"><label for="first_name">First Name *</label>
    <input id="first_name" name="first_name" required></div>
  <div class="field"><label for="last_name">Last Name *</label>
    <input id="last_name" name="last_name" required></div>
  <div class="field"><label for="email">Email *</label>
    <input id="email" name="email" type="email" required></div>
  <div class="field"><label for="phone">Phone</label>
    <input id="phone" name="phone" type="tel"></div>
  <div class="field"><label for="location">Current location (city)</label>
    <input id="location" name="location"></div>
  <div class="field"><label for="linkedin">LinkedIn Profile</label>
    <input id="linkedin" name="linkedin"></div>
  <div class="field"><label for="github">GitHub</label>
    <input id="github" name="github"></div>
  <div class="field"><label for="website">Website</label>
    <input id="website" name="website"></div>
  <div class="field"><label for="prefilled">Referral code</label>
    <input id="prefilled" name="prefilled" value="KEEP-ME"></div>
  <div class="field"><label for="notice">Notice period</label>
    <input id="notice" name="notice"></div>
  <div class="field"><label for="salary">Salary expectation *</label>
    <input id="salary" name="salary" required></div>
  <div class="field"><label for="authorized">Are you legally authorized to work in India?</label>
    <select id="authorized" name="authorized">
      <option value="">Select...</option><option value="yes">Yes</option><option value="no">No</option>
    </select></div>
  <div class="field"><label for="gender">Gender</label>
    <select id="gender" name="gender"><option value="">Select...</option>
      <option>Female</option><option>Male</option><option>Prefer not to say</option></select></div>
  <div class="field"><label for="about">Tell us about yourself</label>
    <textarea id="about" name="about" rows="3"></textarea></div>
  <div class="field"><label for="why">Why are you interested in this role?</label>
    <textarea id="why" name="why" rows="3"></textarea></div>
  <div class="field"><label for="both">Why this role and company?</label>
    <textarea id="both" name="both" rows="3"></textarea></div>
  <div class="field"><label for="resume">Resume/CV *</label>
    <input id="resume" name="resume" type="file" required></div>
  <div class="field"><label><input type="checkbox" name="consent" required style="width:auto">
    I agree to the privacy policy *</label></div>
  <p><button type="submit" id="submit-btn">Submit application</button></p>
</form>
"""


def render(variant: str = "") -> str:
    body = {
        "captcha": _FORM.replace(
            '<p><button', '<div class="g-recaptcha" data-sitekey="test"></div><p><button'),
        "login": (
            '<form><h2>Sign in to continue</h2><label for="u">Email</label><input id="u" name="u">'
            '<label for="p">Password</label><input id="p" type="password" name="p"></form>'
        ),
        "empty": "<p>This posting has no application form.</p>",
    }.get(variant, _FORM)
    return (
        "<!doctype html><html><head><meta charset='utf-8'><title>Test application form</title>"
        f"<style>{_STYLE}</style></head><body>"
        "<h1>Test Co: Frontend Developer</h1>"
        "<p class='note'>CareerPilot local test form. Submitting does nothing real.</p>"
        f"{body}</body></html>"
    )
