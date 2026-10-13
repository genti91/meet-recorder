"""Exercise the Meet participant selectors in a real Chromium DOM."""
import sys

from selenium import webdriver
from selenium.webdriver.chrome.options import Options
from selenium.webdriver.chrome.service import Service

sys.path.insert(0, "/app")
from auto_meet import JS_PARTICIPANTS  # noqa: E402

options = Options()
options.binary_location = "/usr/bin/chromium"
options.add_argument("--headless=new")
options.add_argument("--no-sandbox")
options.add_argument("--disable-dev-shm-usage")
options.add_argument("--window-size=1280,720")
driver = webdriver.Chrome(service=Service("/usr/bin/chromedriver"), options=options)
try:
    driver.get("about:blank")
    driver.execute_script("""
      document.body.innerHTML = `
        <span id="count" style="position:fixed;top:20px;right:20px">9</span>
        <div data-participant-id="one"></div><div data-participant-id="two"></div>`;
    """)
    result = driver.execute_script(JS_PARTICIPANTS)
    assert result == {"count": 9, "source": "corner"}, result

    driver.execute_script("document.getElementById('count').remove()")
    result = driver.execute_script(JS_PARTICIPANTS)
    assert result == {"count": 2, "source": "tiles"}, result

    driver.execute_script("document.body.insertAdjacentHTML('beforeend', '<button aria-label=\"Personas (12)\"></button>')")
    result = driver.execute_script(JS_PARTICIPANTS)
    assert result == {"count": 12, "source": "aria"}, result

    driver.execute_script("document.querySelector('button').remove(); document.querySelector('[data-participant-id]').remove()")
    result = driver.execute_script(JS_PARTICIPANTS)
    assert result == {"count": 1, "source": "tiles"}, result
    print("participant count selectors OK")
finally:
    driver.quit()
