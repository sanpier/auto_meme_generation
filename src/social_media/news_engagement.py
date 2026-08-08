import re
from pathlib import Path
from playwright.sync_api import Page, sync_playwright
from urllib.parse import urlsplit, urlunsplit


BRAVE_PATH = r"C:\Program Files\BraveSoftware\Brave-Browser\Application\brave.exe"
PROJECT_ROOT = Path(r"C:\Users\user\Desktop\Coding\auto_meme_generation")
PROFILE_PATH = PROJECT_ROOT / "data" / "meta_data"


def public_ip(context):
    ip_page = context.new_page()
    ip_page.goto(
        "https://api.ipify.org/",
        wait_until="domcontentloaded",
        timeout=30_000,
    )
    public_ip = ip_page.locator("body").inner_text().strip()
    ip_page.close()
    print("Playwright public IP:", public_ip)

def login_facebook():
    if not Path(BRAVE_PATH).is_file():
        raise FileNotFoundError(f"Brave not found: {BRAVE_PATH}")

    PROFILE_PATH.mkdir(parents=True, exist_ok=True)
    with sync_playwright() as playwright:
        context = playwright.chromium.launch_persistent_context(
            user_data_dir=str(PROFILE_PATH),
            executable_path=BRAVE_PATH,
            headless=False,
            viewport={
                "width": 1440,
                "height": 900,
            },
            screen={
                "width": 1440,
                "height": 900,
            },
            ignore_default_args=["--no-sandbox"],
            locale="en-GB",
            timezone_id="Europe/London",
        )

        context.set_default_timeout(10_000)
        print("Project root:", PROJECT_ROOT)
        print("Profile path:", PROFILE_PATH)
        try:
            # check ip
            public_ip(context)
            # go to page
            page = context.pages[0] if context.pages else context.new_page()
            """
            page.goto(
                "https://www.facebook.com/login/",
                wait_until="domcontentloaded",
                timeout=60_000,
            )
            dismiss_facebook_popups(page)
            print("Facebook opened.")
            print("Giriş açık değilse browser üzerinden manuel olarak giriş yap.")
            input("Facebook ana sayfası hazır olduğunda terminalde Enter'a bas...")
            """
            page.goto("https://www.google.com")
            input("Browser hazır. İstediğin siteye manuel git ve login ol. Sonra Enter.")
            test_tele1_crawling(context)
            input("Sonuçları kontrol ettikten sonra kapatmak için Enter'a bas...")
        finally:
            context.close()

def dismiss_facebook_popups(page: Page) -> None:
    button_names = [
        "Decline optional cookies",
        "Only allow essential cookies",
        "Allow essential and optional cookies",
        "Allow all cookies",
        "Reddet",
        "İsteğe bağlı çerezleri reddet",
        "Yalnızca gerekli çerezlere izin ver",
        "Şimdi değil",
        "Not now",
        "Close",
        "Kapat",
    ]
    for button_name in button_names:
        try:
            button = page.get_by_role("button", name=re.compile(rf"^{re.escape(button_name)}$", re.IGNORECASE))
            if button.count() > 0 and button.first.is_visible():
                button.first.click(timeout=2_000)
                page.wait_for_timeout(500)
        except Exception:
            pass

def canonicalize_facebook_url(url: str) -> str:
    parsed = urlsplit(url)
    path = parsed.path.rstrip("/")
    # story.php gibi bazı URL'lerde gerekli query parametresi bulunur.
    if path.endswith("/story.php") or path == "/story.php":
        allowed_params = []
        for item in parsed.query.split("&"):
            if item.startswith("story_fbid=") or item.startswith("id="):
                allowed_params.append(item)
        query = "&".join(allowed_params)
    else:
        query = ""
    return urlunsplit((parsed.scheme or "https", parsed.netloc, path, query, ""))

def is_facebook_post_url(url: str) -> bool:
    lowered = url.lower()
    if "facebook.com" not in lowered:
        return False

    post_patterns = (
        "/posts/",
        "/videos/",
        "/reel/",
        "/photos/",
        "/permalink/",
        "story_fbid=",
    )
    if not any(pattern in lowered for pattern in post_patterns):
        return False

    # Login, help, share gibi alakasız Facebook linklerini çıkar.
    blocked_patterns = (
        "/login/",
        "/help/",
        "/privacy/",
        "/policies/",
        "/share/",
        "sharer.php",
    )
    if any(pattern in lowered for pattern in blocked_patterns):
        return False

    # Kullanıcı adının URL'de bulunması tercih edilir fakat zorunlu değil.
    # Bazı Facebook permalinkleri numeric Page ID kullanabilir.
    return True

def get_facebook_post_links(
    page: Page,
    page_username: str,
    *,
    max_posts: int = 10,
    scroll_count: int = 4,
) -> list[str]:
    page_url = f"https://www.facebook.com/{page_username}"
    print(f"Opening: {page_url}")
    page.goto(
        page_url,
        wait_until="domcontentloaded",
        timeout=60_000,
    )

    dismiss_facebook_popups(page)

    # İlk postların yüklenmesi için biraz bekle.
    page.wait_for_timeout(3_000)

    links: list[str] = []
    seen: set[str] = set()
    for scroll_index in range(scroll_count + 1):
        hrefs = page.locator("a[href]").evaluate_all(
            """ elements => elements.map(element => element.href).filter(Boolean) """
        )
        for href in hrefs:
            if not is_facebook_post_url(href):
                continue

            clean_url = canonicalize_facebook_url(href)
            if clean_url in seen:
                continue

            seen.add(clean_url)
            links.append(clean_url)
            if len(links) >= max_posts:
                return links

        print(
            f"Scroll {scroll_index}: "
            f"{len(links)} post links collected."
        )
        page.mouse.wheel(0, 1_800)
        page.wait_for_timeout(2_000)
    return links

def test_tele1_crawling(context) -> list[str]:
    page = context.pages[0] if context.pages else context.new_page()
    links = get_facebook_post_links(
        page,
        "tele1comtr",
        max_posts=10,
        scroll_count=5,
    )
    print(f"\nFound {len(links)} links:")

    for index, link in enumerate(links, start=1):
        print(f"{index}. {link}")
    return links


if __name__ == "__main__":
    login_facebook()