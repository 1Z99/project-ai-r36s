"""Offline website checks. No browser, third-party packages, network or device."""

import ast
from collections import Counter
from contextlib import redirect_stderr, redirect_stdout
from html.parser import HTMLParser
import hashlib
import importlib.util
import io
import json
from pathlib import Path
import re
import subprocess
import sys
import tempfile
import unittest
from unittest.mock import patch
from urllib.parse import quote, unquote, urlsplit


REPO = Path(__file__).resolve().parents[1]
DOCS = REPO / "docs"
AI = "projects/227-ai/"
TV = "projects/227-tv/"
PAGES = {"index.html"} | {AI + name for name in (
    "index.html", "guide.html", "reference.html", "development.html", "gallery.html")} | {
        TV + name for name in ("index.html", "guide.html", "reference.html", "development.html", "gallery.html")}
spec = importlib.util.spec_from_file_location("build_site_index", REPO / "tools/build_site_index.py")
indexer = importlib.util.module_from_spec(spec)
spec.loader.exec_module(indexer)


class Document(HTMLParser):
    """Independent inventory for checking the generator against the source HTML."""

    def __init__(self, source):
        super().__init__(convert_charrefs=True)
        self.elements = []
        self.ids = []
        self.sections = []
        self.title_parts = []
        self.style_parts = []
        self.text_parts = []
        self.captures = []
        self.capture_scopes = []
        self.in_title = False
        self.in_style = False
        self.feed(source)
        self.close()

    def handle_starttag(self, tag, attrs):
        attrs = dict(attrs)
        manifest = attrs.get("data-captures", self.capture_scopes[-1][1] if self.capture_scopes else None)
        if "data-shot" in attrs:
            self.captures.append((attrs["data-shot"], manifest))
        if tag not in "area base br col embed hr img input link meta param source track wbr".split():
            self.capture_scopes.append((tag, manifest))
        self.elements.append((tag, attrs))
        if "id" in attrs:
            self.ids.append(attrs["id"])
        if tag == "section" and "data-search-section" in attrs:
            self.sections.append(attrs.get("id"))
        if tag == "title":
            self.in_title = True
        if tag == "style":
            self.in_style = True

    def handle_startendtag(self, tag, attrs):
        self.handle_starttag(tag, attrs)
        self.handle_endtag(tag)

    def handle_endtag(self, tag):
        for index in range(len(self.capture_scopes) - 1, -1, -1):
            if self.capture_scopes[index][0] == tag:
                del self.capture_scopes[index:]
                break
        if tag == "title":
            self.in_title = False
        if tag == "style":
            self.in_style = False

    def handle_data(self, data):
        self.text_parts.append(data)
        if self.in_title:
            self.title_parts.append(data)
        if self.in_style:
            self.style_parts.append(data)

    @property
    def text(self):
        return " ".join(" ".join(self.text_parts).split())


class WebsiteTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.pages = {path.relative_to(DOCS).as_posix(): Document(path.read_text(encoding="utf-8"))
                     for path in sorted(DOCS.rglob("*.html"))}

    def local_target(self, source, value, must_exist=True):
        self.assertIsInstance(value, str)
        self.assertTrue(value.strip(), "{}: empty URL".format(source.name))
        url = urlsplit(value)
        self.assertFalse(url.scheme or url.netloc, "{}: nonlocal URL {!r}".format(source.name, value))
        decoded = unquote(url.path)
        self.assertFalse(decoded.startswith("/") or "\\" in decoded, value)
        target = (source.parent / decoded).resolve() if decoded else source.resolve()
        self.assertTrue(target.is_relative_to(DOCS.resolve()), "URL escapes docs: " + value)
        if must_exist:
            self.assertTrue(target.is_file(), "{}: missing target {!r}".format(source.name, value))
        if url.fragment:
            self.assertEqual(target.suffix, ".html", "Anchor target must be HTML: " + value)
            self.assertIn(unquote(url.fragment), self.pages[target.relative_to(DOCS).as_posix()].ids, value)
        return target

    def test_pages_metadata_landmarks_and_ids(self):
        self.assertTrue(PAGES <= self.pages.keys())
        for name, page in self.pages.items():
            with self.subTest(page=name):
                counts = Counter(tag for tag, _ in page.elements)
                for tag in ("html", "head", "body", "title", "main"):
                    self.assertEqual(counts[tag], 1, tag)
                for tag in ("header", "footer", "h1"):
                    self.assertGreaterEqual(counts[tag], 1, tag)
                self.assertTrue("".join(page.title_parts).strip())
                html = next(attrs for tag, attrs in page.elements if tag == "html")
                self.assertTrue(html.get("lang", "").strip())
                metadata = {attrs.get("name"): attrs.get("content", "")
                            for tag, attrs in page.elements if tag == "meta"}
                self.assertTrue(metadata.get("description", "").strip())
                self.assertRegex(metadata.get("viewport", ""), r"width\s*=\s*device-width")
                self.assertIn("initial-scale=1", metadata["viewport"].replace(" ", ""))
                self.assertTrue(all(page.ids), "Empty ID")
                self.assertEqual(len(page.ids), len(set(page.ids)), "Duplicate IDs")

    def test_links_assets_and_image_alternatives(self):
        for name, page in self.pages.items():
            for tag, attrs in page.elements:
                with self.subTest(page=name, tag=tag, attrs=attrs):
                    self.assertNotIn("ping", attrs, "Tracking link")
                    self.assertNotIn("srcdoc", attrs, "Unexamined embedded document")
                    self.assertNotEqual(tag, "base", "Links must work under a local subdirectory")
                    if tag == "meta":
                        self.assertNotEqual(attrs.get("http-equiv", "").lower(), "refresh")
                    if tag == "img":
                        self.assertIn("alt", attrs)
                        self.assertIsInstance(attrs["alt"], str)
                        image = self.local_target(DOCS / name, attrs.get("src"))
                        self.assertIn(image.suffix.lower(),
                                      (".png", ".jpg", ".jpeg", ".webp", ".avif", ".svg", ".gif", ".ico"),
                                      "Do not emit a broken placeholder image")
                    for attribute in ("href", "src", "poster", "action", "data", "xlink:href"):
                        if attribute not in attrs:
                            continue
                        value = attrs[attribute]
                        url = urlsplit(value or "")
                        if tag == "a" and attribute == "href" and (url.scheme or url.netloc):
                            self.assertIn(url.scheme, ("http", "https", "mailto", "tel"), value)
                            continue
                        self.local_target(DOCS / name, value)
                    if "srcset" in attrs:
                        for candidate in attrs["srcset"].split(","):
                            self.local_target(DOCS / name, candidate.strip().split()[0])
                    if "style" in attrs:
                        self.check_css(DOCS / name, attrs["style"])

    def check_css(self, path, source):
        source = re.sub(r"/\*.*?\*/", "", source, flags=re.S)
        self.assertNotRegex(source, r"(?i)@import\b", "Keep stylesheets self-contained")
        for match in re.finditer(r"url\(\s*(['\"]?)(.*?)\1\s*\)", source, re.I):
            self.local_target(path, match.group(2).strip())

    def test_styles_and_scripts_have_no_external_dependencies_or_trackers(self):
        for path in sorted(DOCS.glob("*.css")):
            with self.subTest(asset=path.name):
                self.check_css(path, path.read_text(encoding="utf-8"))
        for path in sorted(DOCS.glob("*.js")):
            with self.subTest(asset=path.name):
                source = path.read_text(encoding="utf-8")
                self.assertNotRegex(source, r"(?i)(?:https?:)?//[\w.-]+\.[a-z]{2,}")
                self.assertNotRegex(source, r"\b(?:sendBeacon|WebSocket|EventSource|XMLHttpRequest|importScripts)\b")
                self.assertNotRegex(source, r"\b(?:getUserMedia|enumerateDevices|speechSynthesis|webkitSpeechRecognition|RTCPeerConnection)\b")
                self.assertNotRegex(source, r"(?i)google-analytics|googletagmanager|hotjar|plausible|matomo")
                for match in re.finditer(r"new URL\(\s*(['\"])(.*?)\1\s*,\s*document\.baseURI", source):
                    target = self.local_target(path, match.group(2), must_exist=False)
                    self.assertTrue(target.exists(), match.group(2))
        for name, page in self.pages.items():
            self.check_css(DOCS / name, "\n".join(page.style_parts))
            for tag, attrs in page.elements:
                if tag == "script":
                    self.assertTrue(attrs.get("src"), name + ": keep scripts in local files")

    def test_screenshot_manifest_and_placeholder_sources(self):
        selected = {}
        for page in self.pages.values():
            for slot, path in page.captures:
                self.assertTrue(path, "Choose a data-captures manifest for slot " + slot)
                selected.setdefault(path, set()).add(slot)
        self.assertTrue(selected)
        for path, slots in selected.items():
            manifest_path = self.local_target(DOCS / "site.js", path)
            manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
            self.assertIsInstance(manifest, dict)
            self.assertTrue(slots <= manifest.keys(), path + ": missing screenshot manifest keys")
            for key, entry in manifest.items():
                with self.subTest(manifest=path, slot=key):
                    self.assertIsInstance(entry, dict)
                    self.assertIsInstance(entry.get("alt"), str)
                    self.assertTrue(entry["alt"].strip())
                    self.assertIn("src", entry)
                    self.assertIn(entry.get("kind"), ("photo", "screenshot"))
                    for field in ("src", "suggestedFile"):
                        value = entry.get(field)
                        if value is None:
                            continue
                        target = self.local_target(DOCS / "site.js", value, must_exist=field == "src")
                        url = urlsplit(value)
                        self.assertFalse(url.query or url.fragment, value)
                        self.assertTrue(target.is_relative_to((DOCS / "media").resolve()), value)
                        self.assertTrue(target.parent.is_dir(), value)
                        self.assertIn(target.suffix.lower(), (".png", ".jpg", ".jpeg", ".webp", ".avif", ".svg"))

    def test_capture_manifests_follow_project_and_card_scopes(self):
        page = Document('''<body data-captures="projects/first/screenshots.json">
<figure data-shot="chat"></figure>
<article data-captures="projects/second/screenshots.json">
<figure data-shot="chat"><img src="media/second/chat.png"></figure></article>
<figure data-shot="keyboard"></figure></body>''')
        self.assertEqual(page.captures, [
            ("chat", "projects/first/screenshots.json"),
            ("chat", "projects/second/screenshots.json"),
            ("keyboard", "projects/first/screenshots.json"),
        ])

    def test_tv_gallery_and_capture_provenance(self):
        manifest = json.loads((DOCS / TV / "screenshots.json").read_text())
        metadata = json.loads((DOCS / "media/227-tv/captures.json").read_text())
        gallery = self.pages[TV + "gallery.html"]
        slots = [attrs["data-shot"] for _, attrs in gallery.elements if "data-shot" in attrs]
        self.assertEqual(slots, ["world", "country", "search", "watch", "matrix", "settings",
                                 "quality", "installer", "confirm"])
        self.assertEqual(set(slots), set(manifest))
        self.assertEqual(set(slots), {entry["id"] for entry in metadata["captures"]})
        self.assertTrue(metadata["productionRenderer"] and metadata["lossless"])
        self.assertFalse(metadata["fictionalChannels"] or metadata["stillPhotographs"])
        for flag in ("realPlayback", "liveStreams", "videoDecoding", "networkAccess"):
            self.assertTrue(metadata[flag], flag)
        for flag in ("audioPlayback", "deviceAccess",
                     "controllerAccess", "userPersistence", "userStateRead", "installation"):
            self.assertFalse(metadata[flag], flag)
        for entry in metadata["captures"]:
            image = (DOCS / manifest[entry["id"]]["src"]).read_bytes()
            self.assertEqual(image[:8], b"\x89PNG\r\n\x1a\n")
            self.assertEqual((int.from_bytes(image[16:20], "big"), int.from_bytes(image[20:24], "big")), (640, 480))
            self.assertEqual(len(image), entry["bytes"])
            self.assertEqual(hashlib.sha256(image).hexdigest(), entry["sha256"])
            self.assertLess(len(image), 1024*1024)
            self.assertEqual(manifest[entry["id"]]["origin"], "pc-demo")
            self.assertEqual(manifest[entry["id"]]["method"], "native-render")
            self.assertEqual(bool(manifest[entry["id"]].get("realPlayback")), entry["realPlayback"])
            self.assertEqual(entry["playbackVisible"], entry["id"] in ("watch", "matrix"))
            self.assertEqual(entry["realPlayback"], entry["id"] in ("watch", "matrix", "settings", "quality"))
            for player in entry["playback"]:
                self.assertGreaterEqual(player["decodedFrameCount"], 8)
                self.assertGreaterEqual(player["distinctFrameCount"], 3)
                self.assertGreater(player["sourceWidth"], 0)
                self.assertGreater(player["sourceHeight"], 0)
                self.assertFalse(player["status"]["error"])
                self.assertIn(player["audioOutput"], ("null", "disabled"))
                self.assertTrue(player["playbackURL"].startswith("https://live.france24.com/"))
        matrix = next(entry for entry in metadata["captures"] if entry["id"] == "matrix")
        self.assertEqual(matrix["distinctChannelCount"], 4)
        self.assertEqual({player["language"] for player in matrix["playback"]},
                         {"French", "English", "Arabic", "Spanish"})
        self.assertEqual(list((DOCS/"media/227-tv").rglob("*.jpg")), [])
        for phrase in ("France 24", "real", "decoded", "PC", "NOTICE.md"):
            self.assertIn(phrase, gallery.text)

    def test_tv_navigation_and_documented_boundaries(self):
        for name in (name for name in self.pages if name.startswith(TV)):
            source = (DOCS / name).read_text()
            nav = Document(re.search(r'<nav class="project-nav"[^>]*>(.*?)</nav>', source, re.S).group(1))
            links = [attrs for tag, attrs in nav.elements if tag == "a"]
            self.assertEqual({attrs["href"] for attrs in links}, {
                "index.html", "guide.html", "reference.html", "development.html", "gallery.html"})
            current = [attrs for attrs in links if attrs.get("aria-current") == "page"]
            self.assertEqual(len(current), 1)
            self.assertEqual(self.local_target(DOCS/name, current[0]["href"]), DOCS/name)
        guide = self.pages[TV+"guide.html"].text
        reference = self.pages[TV+"reference.html"].text
        for phrase in ("1.0.4", "installed-release.json", "same-version", "state.json", "Troubleshooting"):
            self.assertIn(phrase, guide)
        for phrase in ("local filter, not worldwide", "298 x 218", "2.5 seconds", "50", "HTTP as well as HTTPS"):
            self.assertIn(phrase, reference)

    def test_documented_build_versions_and_core_pins(self):
        development = self.pages[AI + "development.html"].text
        for value in ("R36S", "Panel 4", "c83568c", "dArkOSRE", "03082026", "4.4.189", "aarch64",
                      "3.13.5", "/roms", "exFAT", "mmcblk0p3", "1.2.0", "v1.0.0",
                      "0.7.0-dev", "11669", "620d2225c"):
            with self.subTest(value=value):
                self.assertIn(value, development)
        # Read literal pins without importing the installer or initializing any runtime.
        tree = ast.parse((REPO / "installer/installer_core.py").read_text(encoding="utf-8"))
        constants = {node.targets[0].id: ast.literal_eval(node.value)
                     for node in tree.body if isinstance(node, ast.Assign)
                     and isinstance(node.targets[0], ast.Name)
                     and node.targets[0].id in ("RUNTIME", "RUNTIME_SHA", "MODELS")}
        reference = self.pages[AI + "reference.html"].text
        for filename, digest in [(constants["RUNTIME"], constants["RUNTIME_SHA"])] + [
                (model[0], model[1]) for model in constants["MODELS"]]:
            with self.subTest(payload=filename):
                self.assertIn(filename, reference)
                self.assertIn(digest, reference)
        self.assertRegex(development.lower(), r"(?:not tested|not yet verified|unverified)")
        self.assertRegex(development.lower(), r"(?:does not certify|not a blanket|not every)")
        for page in self.pages.values():
            self.assertNotRegex(page.text.lower(), r"\b(?:universally compatible|guaranteed compatibility|works on every (?:r36s|firmware|handheld))\b")

    def test_homepage_is_creator_first_and_preserves_handheld_art(self):
        home = self.pages["index.html"]
        self.assertTrue("".join(home.title_parts).startswith("1Z99 |"))
        for section in ("projects", "about", "device", "project-ai"):
            self.assertIn(section, home.ids)
        for value in ("1Z99", "native", "227 AI", "Panel 4"):
            self.assertIn(value.lower(), home.text.lower())
        self.assertTrue(any(tag == "svg" and "handheld-illustration" in attrs.get("class", "").split()
                            for tag, attrs in home.elements))
        screen = next(attrs for tag, attrs in home.elements if tag == "image" and attrs.get("class") == "device-demo-poster")
        self.assertEqual((screen["x"], screen["y"], screen["width"], screen["height"]), ("72", "56", "216", "162"))
        self.assertEqual(screen["href"], "media/previews/app/chat.png")
        preview = DOCS / screen["href"]
        image_data = preview.read_bytes()
        self.assertEqual(image_data[:8], b"\x89PNG\r\n\x1a\n")
        self.assertEqual(int.from_bytes(image_data[16:20], "big"), 640)
        self.assertEqual(int.from_bytes(image_data[20:24], "big"), 480)
        self.assertFalse(any("data-install-command" in attrs for _, attrs in home.elements))
        self.assertTrue(any("data-install-command" in attrs for _, attrs in self.pages[AI + "guide.html"].elements))

    def test_personal_site_identity_and_project_scoped_navigation(self):
        for name, page in self.pages.items():
            source = (DOCS / name).read_text(encoding="utf-8")
            with self.subTest(page=name):
                self.assertTrue(any(tag == "a" and attrs.get("aria-label") == "1Z99 home"
                                    for tag, attrs in page.elements))
                navigation = Document(re.search(r'<nav class="main-nav"[^>]*>(.*?)</nav>', source, re.S).group(1))
                self.assertEqual(navigation.text, "Projects About My R36S")
                for tag, attrs in navigation.elements:
                    if tag == "a":
                        self.assertEqual(self.local_target(DOCS / name, attrs["href"]), DOCS / "index.html")
                self.assertTrue(any("data-search-open" in attrs and attrs.get("aria-label") == "Search site"
                                    for _, attrs in page.elements))
                if name.startswith(AI):
                    self.assertIn("| 227 AI | 1Z99", "".join(page.title_parts))
                    self.assertIn('aria-label="Breadcrumb"', source)
                    self.assertIn('data-project="227-ai"', source)
                    project_nav = Document(re.search(r'<nav class="project-nav"[^>]*>(.*?)</nav>', source, re.S).group(1))
                    links = [attrs for tag, attrs in project_nav.elements if tag == "a"]
                    self.assertEqual({attrs["href"] for attrs in links}, {
                        "index.html", "guide.html", "reference.html", "development.html", "gallery.html"})
                    current = [attrs for attrs in links if attrs.get("aria-current") == "page"]
                    self.assertEqual(len(current), 1)
                    self.assertEqual(self.local_target(DOCS / name, current[0]["href"]), DOCS / name)
        home_source = (DOCS / "index.html").read_text(encoding="utf-8")
        home_body = next(attrs for tag, attrs in self.pages["index.html"].elements if tag == "body")
        self.assertNotIn("data-project", home_body, "The personal homepage is not an app page")
        self.assertNotIn("data-captures", home_body, "Each featured project selects its own captures")
        self.assertIn('href="projects/227-ai/index.html"', home_source)
        self.assertIn('href="projects/227-ai/development.html#tested-build">227 AI tested environment', home_source)
        self.assertNotIn("Full tested environment", home_source)
        for old_page in ("guide.html", "reference.html", "development.html", "gallery.html"):
            self.assertFalse((DOCS / old_page).exists(), "App-only pages must not remain site-root pages")

    def test_installation_separates_two_easy_steps_from_ssh(self):
        guide_source = (DOCS / AI / "guide.html").read_text(encoding="utf-8")
        easy_steps = re.search(r'<ol class="steps" data-easy-install>(.*?)</ol>',
                               guide_source, re.S)
        self.assertIsNotNone(easy_steps)
        self.assertEqual(easy_steps.group(1).count("<li>"), 2)
        easy_text = Document(easy_steps.group(1)).text
        readme = (REPO / "README.md").read_text(encoding="utf-8")
        easy_readme = readme.split("### Easy Install (No Commands Needed)", 1)[1].split(
            "### Advanced Install (SSH)", 1)[0]
        self.assertEqual(re.findall(r"^\d+\.", easy_readme, re.M), ["1.", "2."])
        for text in (easy_text, " ".join(easy_readme.split())):
            with self.subTest(text=text):
                self.assertIn("SD card you use for games", text)
                self.assertIn("tools", text)
                self.assertIn("Wi-Fi", text)
                self.assertIn("INSTALL", text)
                self.assertIn("wait for success", text)
                self.assertIn("Only then restart the handheld", text)
                self.assertNotIn("chmod", text)
                self.assertNotIn("sh install227ai.sh", text)
        for text in (guide_source, readme):
            self.assertNotIn("Mark the script executable", text)
            self.assertNotIn("Mark it executable", text)
            self.assertIn("Advanced Install (SSH)", text)
            self.assertIn("ssh YOUR_USER@YOUR_HANDHELD_IP", text)
            self.assertIn("sh /roms/tools/install227ai.sh", text)
        self.assertLess(guide_source.index('id="easy-install"'),
                        guide_source.index('id="advanced-install"'))

    def test_docs_explain_current_bootstrap_and_legacy_menu_cleanup(self):
        for name in ("guide.html", "reference.html", "development.html"):
            with self.subTest(page=name):
                self.assertIn("install.bootstrap", self.pages[AI + name].text)
                self.assertNotIn("tools/227-ai-manager/install.sh", self.pages[AI + name].text)
        guide = self.pages[AI + "guide.html"]
        self.assertIn("manager-menu", guide.ids)
        self.assertIn("same Tools card as your existing manager", guide.text)
        self.assertIn("No app reinstall or manual folder deletion is needed", guide.text)
        self.assertIn("Update / Repair does not update the manager", guide.text)
        self.assertIn("The app prefers bundled fonts, with OS fonts as fallback", self.pages[AI + "reference.html"].text)

    def test_homepage_keeps_original_style_with_requested_deletions(self):
        home = self.pages["index.html"]
        self.assertTrue(any(tag == "ol" and attrs.get("class") == "project-cards" for tag, attrs in home.elements))
        self.assertGreaterEqual(sum(tag == "article" and attrs.get("class") == "project-feature" for tag, attrs in home.elements), 1)
        self.assertTrue(any(tag == "nav" and attrs.get("class") == "main-nav" for tag, attrs in home.elements))
        self.assertTrue(any(tag == "section" and attrs.get("class") == "workshop-intro" for tag, attrs in home.elements))
        self.assertFalse(any("primary" in attrs.get("class", "").split() for _, attrs in home.elements))
        for phrase in ("Explore my projects", "Read the build notes", "ABOUT THE WORKSHOP", "On my workbench",
                       "STILL EXPLORING", "Start with the hardware", "More possibilities"):
            self.assertNotIn(phrase.lower(), home.text.lower())
        for page in self.pages.values():
            self.assertFalse(any("data-theme-toggle" in attrs for _, attrs in page.elements))
            self.assertEqual(sum("data-menu-toggle" in attrs for _, attrs in page.elements), 1)

    def test_native_demo_screenshots_are_complete_and_labeled(self):
        manifest = json.loads((DOCS / "screenshots.json").read_text())
        slots = ("installer", "confirm", "download", "success", "chat", "keyboard",
                 "speech", "voice", "dictation", "history", "prompt", "settings")
        for key in slots:
            entry = manifest[key]
            self.assertEqual(entry["origin"], "pc-demo")
            self.assertIn("PC demo", entry["alt"])
            self.assertEqual(entry["method"], "native-render")
            self.assertNotIn("time", entry, "Native stills are not video frame extracts")
            image = (DOCS / entry["src"]).read_bytes()
            self.assertEqual(image[:8], b"\x89PNG\r\n\x1a\n")
            self.assertEqual((int.from_bytes(image[16:20], "big"), int.from_bytes(image[20:24], "big")), (640, 480))
            self.assertLess(len(image), 1024 * 1024)
        app = json.loads((DOCS / "media/previews/app/captures.json").read_text())
        self.assertTrue(app["productionRenderer"] and app["lossless"] and app["sampleReplies"])
        for flag in ("liveModel", "microphoneAccess", "deviceAccess", "networkAccess", "userPersistence"):
            self.assertFalse(app[flag])
        for capture in app["captures"]:
            image = (DOCS / manifest[capture["id"]]["src"]).read_bytes()
            self.assertEqual(hashlib.sha256(image).hexdigest(), capture["sha256"])
            self.assertEqual(len(image), capture["bytes"])

    def test_gallery_shows_installer_before_app_and_has_safe_demo_video(self):
        gallery = self.pages[AI + "gallery.html"]
        slots = [attrs["data-shot"] for _, attrs in gallery.elements if "data-shot" in attrs]
        self.assertEqual(slots, ["installer", "confirm", "download", "success", "chat", "keyboard",
                                 "speech", "voice", "dictation", "history", "prompt", "settings"])
        video = next(attrs for tag, attrs in gallery.elements if tag == "video")
        self.assertIn("controls", video)
        self.assertIn("playsinline", video)
        self.assertNotIn("autoplay", video)
        self.assertEqual(video["preload"], "none")
        self.assertIn("simulated", gallery.text)
        self.assertIn("Nothing is installed or downloaded", gallery.text)
        demo = json.loads((DOCS / "media/previews/installer/demo.json").read_text())
        self.assertEqual((demo["width"], demo["height"], demo["duration"]), (640, 480, 14))
        self.assertTrue(demo["productionRenderer"] and demo["simulatedInstall"] and demo["simulatedDownloads"])
        for flag in ("packageChanges", "networkAccess", "deviceAccess", "realWorker", "hasAudio"):
            self.assertFalse(demo[flag])
        clip = (DOCS / "media/previews/installer" / demo["video"]).read_bytes()
        self.assertEqual(clip[4:8], b"ftyp")
        self.assertLess(len(clip), 1024 * 1024)

    def test_walkthrough_is_local_captioned_and_explicitly_simulated(self):
        home = self.pages["index.html"]
        video = next(attrs for tag, attrs in home.elements if tag == "video")
        for flag in ("muted", "playsinline", "loop", "controls"):
            self.assertIn(flag, video)
        self.assertNotIn("autoplay", video, "Autoplay must respect reduced-motion preferences")
        viewport = next(attrs for tag, attrs in home.elements if tag == "foreignobject")
        self.assertEqual((viewport["x"], viewport["y"], viewport["width"], viewport["height"]), ("72", "56", "216", "162"))
        self.assertTrue(any(tag == "track" and attrs.get("kind") == "captions" for tag, attrs in home.elements))
        metadata = json.loads((DOCS / "media/previews/walkthrough.json").read_text())
        self.assertEqual((metadata["width"], metadata["height"], metadata["duration"]), (640, 480, 78))
        self.assertTrue(metadata["hasAudio"])
        self.assertTrue(metadata["sampleReplies"])
        self.assertTrue(metadata["simulatedMic"])
        self.assertTrue(metadata["prerecordedMicPlayback"])
        self.assertFalse(metadata["liveModel"])
        self.assertFalse(metadata["microphoneAccess"])
        self.assertFalse(metadata["deviceAccess"])
        self.assertEqual(metadata["chapters"][0]["start"], 0)
        self.assertEqual(metadata["chapters"][-1]["end"], metadata["duration"])
        self.assertEqual(len(metadata["chapters"]), 9)
        video_bytes = (DOCS / "media/previews" / metadata["video"]).read_bytes()
        self.assertEqual(video_bytes[4:8], b"ftyp")
        self.assertLess(len(video_bytes), 5 * 1024 * 1024)
        self.assertIn("simulated microphone", home.text)
        self.assertIn("Sound starts muted", home.text)

    def test_search_index_is_current_and_covers_every_section(self):
        expected = indexer.build_index()
        path = DOCS / "search-index.json"
        self.assertTrue(path.is_file(), "Run python3 tools/build_site_index.py")
        self.assertEqual(path.read_bytes(), indexer.render_index(expected).encode("ascii"),
                         "Search index is stale: run python3 tools/build_site_index.py")
        actual = json.loads(path.read_text(encoding="ascii"))
        sections = {"{}#{}".format(quote(name, safe="/"), quote(anchor, safe=""))
                    for name, page in self.pages.items() for anchor in page.sections}
        self.assertEqual({entry["url"] for entry in actual}, sections)
        self.assertEqual(len(actual), len(sections))
        for entry in actual:
            with self.subTest(url=entry["url"]):
                self.assertEqual(set(entry), {"page", "title", "url", "text"})
                for value in entry.values():
                    self.assertIsInstance(value, str)
                    self.assertTrue(value.strip())
                    self.assertEqual(value, " ".join(value.split()))
                target = self.local_target(path, entry["url"])
                page = self.pages[target.relative_to(DOCS).as_posix()]
                title_parts = [part.strip() for part in "".join(page.title_parts).split("|")]
                expected_label = " / ".join(reversed(title_parts[:-1])) if len(title_parts) > 2 else title_parts[0]
                self.assertEqual(entry["page"], expected_label)

    def test_index_check_works_outside_repository_without_writes(self):
        output = DOCS / "search-index.json"
        before = output.read_bytes()
        timestamp = output.stat().st_mtime_ns
        with tempfile.TemporaryDirectory(prefix="227 website cwd ") as directory:
            result = subprocess.run([sys.executable, "-B", str(REPO / "tools/build_site_index.py"), "--check"],
                                    cwd=directory, capture_output=True, text=True, timeout=10)
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(output.read_bytes(), before)
        self.assertEqual(output.stat().st_mtime_ns, timestamp)


class SearchGeneratorTest(unittest.TestCase):
    def test_multiple_projects_can_have_the_same_page_names_and_anchors(self):
        with tempfile.TemporaryDirectory(prefix="1z99 projects ") as directory:
            docs = Path(directory)
            for slug, title in (("227-ai", "227 AI"), ("another app", "Another App")):
                project = docs / "projects" / slug
                project.mkdir(parents=True)
                (project / "index.html").write_text(
                    '<title>Overview | {} | 1Z99</title><section id="overview" data-search-section>'
                    '<h1>Overview</h1>Independent project</section>'.format(title), encoding="utf-8")
            entries = indexer.build_index(docs)
        self.assertEqual({entry["url"] for entry in entries}, {
            "projects/227-ai/index.html#overview", "projects/another%20app/index.html#overview"})
        self.assertEqual({entry["page"] for entry in entries}, {
            "227 AI / Overview", "Another App / Overview"})

    def test_static_sections_headings_exclusions_and_escaped_urls(self):
        with tempfile.TemporaryDirectory(prefix="227 search ") as directory:
            docs = Path(directory)
            (docs / "z.html").write_text('<title>Other | Site</title><section id="last" data-search-section><h1>Last</h1>Text</section>', encoding="utf-8")
            (docs / "a page.html").write_text('''<title>Example &amp; Test | Site</title>
<section id="caf\u00e9%" data-search-section><h2>A <em>heading</em><br>here</h2>
<p>Useful   text &amp; entities.</p><script>script-noise</script><style>style-noise</style>
<nav>nav-noise</nav><button>button-noise</button><svg><text>svg-noise</text></svg>
<figure><div class="shot shot-placeholder">placeholder-noise</div><figcaption>Real caption</figcaption></figure></section>
<section id="fallback_name-here" data-search-section><p>Fallback caf\u00e9</p></section>
<section id="h1" data-search-section><h1>First heading</h1><h2>Later heading</h2>Body</section>
<section id="not-indexed">Not searchable</section>''', encoding="utf-8")
            (docs / "nested").mkdir()
            (docs / "nested/guide.html").write_text('<title>Guide | Another App | 1Z99</title><section id="nested" data-search-section><h2>Nested</h2>Project text</section>', encoding="utf-8")
            entries = indexer.build_index(docs)
        self.assertEqual(len(entries), 5)
        self.assertEqual(entries[0], {
            "page": "Example & Test", "title": "A heading here", "url": "a%20page.html#caf%C3%A9%25",
            "text": "A heading here Useful text & entities. Real caption",
        })
        self.assertEqual(entries[1]["title"], "Fallback Name Here")
        self.assertEqual(entries[2]["title"], "First heading")
        self.assertEqual(entries[3]["page"], "Another App / Guide")
        self.assertEqual(entries[3]["url"], "nested/guide.html#nested")
        self.assertEqual(entries[4]["url"], "z.html#last")
        rendered = indexer.render_index(entries)
        self.assertTrue(rendered.isascii())
        self.assertIn(r"\u00e9", rendered)
        self.assertTrue(rendered.endswith("\n"))
        self.assertEqual(json.loads(rendered), entries)

    def test_check_rejects_missing_and_stale_index_without_writing(self):
        with tempfile.TemporaryDirectory(prefix="227 index check ") as directory:
            docs = Path(directory)
            (docs / "index.html").write_text('<title>Test</title><section id="test" data-search-section>Text</section>', encoding="utf-8")
            output = docs / "search-index.json"
            with patch.object(indexer, "DOCS", docs), patch.object(indexer, "build_index", return_value=indexer.build_index(docs)), \
                    redirect_stdout(io.StringIO()), redirect_stderr(io.StringIO()):
                self.assertEqual(indexer.main(["--check"]), 1)
                self.assertFalse(output.exists())
                self.assertEqual(indexer.main([]), 0)
                expected = output.read_bytes()
                self.assertEqual(indexer.main(["--check"]), 0)
                output.write_bytes(b"[]\n")
                self.assertEqual(indexer.main(["--check"]), 1)
                self.assertEqual(output.read_bytes(), b"[]\n")
                self.assertEqual(indexer.main([]), 0)
                self.assertEqual(output.read_bytes(), expected)


if __name__ == "__main__":
    unittest.main()
