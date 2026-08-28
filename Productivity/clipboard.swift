#!/usr/bin/env swift

// <vee.title>Clipboard</vee.title>
// <vee.version>3.1</vee.version>
// <vee.author>Naveen Kumar</vee.author>
// <vee.author.github>navbytes</vee.author.github>
// <vee.desc>Clipboard history — full-text fuzzy search, pins, and a global hotkey. Return copies a match back to the clipboard; ⌥ turns each row into Pin/Unpin.</vee.desc>
// <vee.dependencies>Xcode Command Line Tools (provides /usr/bin/swift; macOS does not ship it)</vee.dependencies>
// <vee.abouturl>https://github.com/navbytes/vee-plugins</vee.abouturl>
//
// <vee.type>streamable</vee.type>
//
// Needs the Xcode Command Line Tools (`xcode-select --install`) — macOS does
// not ship `swift` by default, so this plugin won't run without them.
//
// Search panel + global hotkey (Vee-native): press the hotkey anywhere, type to
// filter across the FULL text of every entry, Return to put it on the clipboard.
// <vee.filter>true</vee.filter>
// <vee.shortcut>cmd+ctrl+v</vee.shortcut>
// (cmd+shift+v is "Paste and Match Style" almost everywhere on macOS — grabbing
// it globally breaks that in every other app. Rebind from the plugin's
// Settings if cmd+ctrl+v collides with something on your machine.)
//
// <vee.var>number(VEE_CLIP_MAX=40): Max unpinned entries kept (oldest evicted past this). Pins are never auto-evicted by count.</vee.var>
// <vee.var>number(VEE_CLIP_MAX_KB=200): Skip clips larger than this (KB).</vee.var>
// <vee.var>boolean(VEE_CLIP_AUTOPASTE=false): After selecting, simulate Cmd+V to paste into the front app (needs Accessibility permission for Vee).</vee.var>
// <vee.var>number(VEE_CLIP_MAX_AGE_DAYS=7): Auto-delete unpinned entries older than this many days (0 = keep forever). Everything you copy is cached in plaintext under ~/Library/Caches/vee-clipboard, so this bounds how long that lives.</vee.var>
// <vee.var>string(VEE_CLIP_IGNORE_APPS=): Comma-separated app names to never capture from, e.g. "1Password,Terminal".</vee.var>

//
// Trust footprint — reads everything you copy; keep that in mind:
// <vee.capabilities>clipboard</vee.capabilities>
// <vee.exec>bash,cat,pbcopy,osascript,touch,rm,basename</vee.exec>
// <vee.filesystem.read>~/Library/Caches/vee-clipboard</vee.filesystem.read>
// <vee.filesystem.write>~/Library/Caches/vee-clipboard</vee.filesystem.write>

import AppKit
import ApplicationServices
import CryptoKit
import Foundation

setvbuf(stdout, nil, _IONBF, 0) // unbuffered → streamed menu updates flush at once

let fm = FileManager.default
let dir = fm.homeDirectoryForCurrentUser.path + "/Library/Caches/vee-clipboard"
do {
  try fm.createDirectory(atPath: dir, withIntermediateDirectories: true)
  try fm.setAttributes([.posixPermissions: 0o700], ofItemAtPath: dir)
} catch {
  log("clipboard: cache dir setup failed: \(error)")
}
let manifestPath = dir + "/manifest"
let helperPath = dir + "/clip.sh"

let env = ProcessInfo.processInfo.environment
let maxItems = max(1, Int(env["VEE_CLIP_MAX"] ?? "40") ?? 40)
let maxBytes = max(1, Int(env["VEE_CLIP_MAX_KB"] ?? "200") ?? 200) * 1024
let autopaste = env["VEE_CLIP_AUTOPASTE"]?.lowercased() == "true" ? "1" : "0"
let maxAgeDays = max(0, Int(env["VEE_CLIP_MAX_AGE_DAYS"] ?? "7") ?? 7)

let ignoreApps: Set<String> = Set(
  (env["VEE_CLIP_IGNORE_APPS"] ?? "")
    .split(separator: ",")
    .map { $0.trimmingCharacters(in: .whitespaces).lowercased() }
    .filter { !$0.isEmpty }
)
let searchCap = 1000 // chars of each entry made searchable (bounds render output)

// A pin is just the presence of "<id>.pin" — no manifest schema for it, so the
// bash helper can toggle it and the loop reconciles on reload.
func pinPath(_ id: String) -> String { dir + "/\(id).pin" }
func isPinned(_ id: String) -> Bool { fm.fileExists(atPath: pinPath(id)) }

func log(_ msg: String) { FileHandle.standardError.write((msg + "\n").data(using: .utf8) ?? Data()) }

// ── Helper script: copy / optional paste / pin / unpin / clear ───────────────
let helper = """
#!/bin/bash
d="$(dirname "$0")"
case "$1" in
  copy)
    [ -n "$2" ] && [[ "$2" =~ ^[0-9]+$ ]] || exit 0
    f="$d/$2.txt"; [ -f "$f" ] || exit 0
    /bin/cat "$f" | /usr/bin/pbcopy
    [ "$3" = "1" ] && /usr/bin/osascript -e 'tell application "System Events" to keystroke "v" using command down'
    ;;
  pin)
    [ -n "$2" ] && [[ "$2" =~ ^[0-9]+$ ]] || exit 0
    /usr/bin/touch "$d/$2.pin"; /usr/bin/touch "$d/manifest" ;;
  unpin)
    [ -n "$2" ] && [[ "$2" =~ ^[0-9]+$ ]] || exit 0
    /bin/rm -f "$d/$2.pin"; /usr/bin/touch "$d/manifest" ;;
  clear)
    if /usr/bin/osascript -e 'display dialog "Clear all unpinned clipboard history?" buttons {"Cancel","Clear"} default button "Cancel" with icon caution' >/dev/null 2>&1; then
      for f in "$d"/*.txt; do id="$(/usr/bin/basename "$f" .txt)"; [ -f "$d/$id.pin" ] || /bin/rm -f "$f"; done
      /usr/bin/touch "$d/manifest"
    fi
    ;;
esac
"""
do {
  let existing = try? String(contentsOfFile: helperPath, encoding: .utf8)
  if existing != helper {
    try helper.write(toFile: helperPath, atomically: true, encoding: .utf8)
    try fm.setAttributes([.posixPermissions: 0o755], ofItemAtPath: helperPath)
  }
} catch {
  log("clipboard: helper script write failed: \(error)")
}

// ── History model (disk is the source of truth) ──────────────────────────────
// manifest line: id \t ts \t app \t icon \t hash \t searchtext(<=searchCap, single-line)
// The .txt file holds the exact original content (for copy-back). `searchtext`
// is the full-ish content made searchable AND displayed (truncated via length=),
// already escaped for the line format (see `oneLine`) — it is written to disk
// pre-escaped, so every reader of `Entry.text` gets a safe-to-print string.
struct Entry { let id, hash, app, icon, text: String; var ts: Int; var pinned: Bool }

func hash(_ s: String) -> String {
  SHA256.hash(data: Data(s.utf8)).prefix(8).map { String(format: "%02x", $0) }.joined()
}

// Collapse to one line, cap length, THEN escape — escaping after truncation
// would risk cutting a `\|`/`\\` pair in half. Escape order matters: backslash
// first (`\` → `\\`), then pipe (`|` → `\|`) — reversing it would double-escape
// the backslash the pipe-escape just introduced. Matches
// `LineEscape.unescape`: `\|`→`|`, `\n`→newline, `\\`→`\`.
func oneLine(_ s: String) -> String {
  var t = s
  for (a, b) in [("\t", " "), ("\n", " "), ("\r", " ")] { t = t.replacingOccurrences(of: a, with: b) }
  t = t.split(separator: " ", omittingEmptySubsequences: true).joined(separator: " ")
  if t.count > searchCap { t = String(t.prefix(searchCap)) }
  t = t.replacingOccurrences(of: "\\", with: "\\\\")
  t = t.replacingOccurrences(of: "|", with: "\\|")
  return t
}

func iconFor(_ text: String) -> String {
  let t = text.trimmingCharacters(in: .whitespacesAndNewlines)
  if t.hasPrefix("http://") || t.hasPrefix("https://") { return "link" }
  if t.range(of: #"^[^@\s]+@[^@\s]+\.[^@\s]+$"#, options: .regularExpression) != nil { return "envelope" }
  if t.range(of: #"^#?[0-9A-Fa-f]{6}$"#, options: .regularExpression) != nil { return "paintpalette" }
  return "doc.on.doc"
}

func relTime(_ ts: Int) -> String {
  if ts == 0 { return "" }
  let s = Int(Date().timeIntervalSince1970) - ts
  if s < 60 { return "just now" }
  if s < 3600 { return "\(s / 60)m ago" }
  if s < 86400 { return "\(s / 3600)h ago" }
  return "\(s / 86400)d ago"
}

func loadManifest() -> [Entry] {
  guard let s = try? String(contentsOfFile: manifestPath, encoding: .utf8) else { return [] }
  return s.split(separator: "\n").compactMap {
    let p = $0.components(separatedBy: "\t")
    guard p.count >= 6 else { return nil }
    let id = p[0]
    guard !id.isEmpty, id.allSatisfy({ $0.isNumber }) else { return nil }
    guard fm.fileExists(atPath: dir + "/\(id).txt") else { return nil }
    // Support old 7-field (id,ts,app,icon,hash,expiresAt,text) and new 6-field (text=p[5])
    let text: String
    if p.count >= 7 { text = p[6] } else { text = p[5] }
    return Entry(id: id, hash: p[4], app: p[2], icon: p[3], text: text,
                 ts: Int(p[1]) ?? 0, pinned: isPinned(id))
  }
}

func saveManifest(_ entries: [Entry]) {
  let s = entries.map { "\($0.id)\t\($0.ts)\t\($0.app)\t\($0.icon)\t\($0.hash)\t\($0.text)" }
    .joined(separator: "\n")
  do {
    try s.write(toFile: manifestPath, atomically: true, encoding: .utf8)
  } catch {
    log("clipboard: saveManifest failed: \(error)")
  }
}

func mtime(_ path: String) -> Double {
  ((try? fm.attributesOfItem(atPath: path))?[.modificationDate] as? Date)?.timeIntervalSince1970 ?? 0
}

// ── Rendering (Vee streamable format) ─────────────────────────────────────────
func quote(_ v: String) -> String {
  guard v.contains(where: { $0 == " " || $0 == "|" || $0 == "\"" || $0 == "\\" }) else { return v }
  var t = v
  t = t.replacingOccurrences(of: "\\", with: "\\\\") // backslash first, same rule as `oneLine`
  t = t.replacingOccurrences(of: "\"", with: "\\\"")
  t = t.replacingOccurrences(of: "|", with: "\\|")
  return "\"" + t + "\""
}
// `s` is already `oneLine`-escaped. Truncate on chars, but if the cut lands
// mid-escape (right after a lone trailing `\` of a `\|`/`\\` pair) drop that
// dangling backslash too — otherwise the ellipsis gets glued onto half an
// escape sequence.
func short(_ s: String) -> String {
  guard s.count > 30 else { return s }
  var t = String(s.prefix(29))
  if t.hasSuffix("\\") { t.removeLast() }
  return t + "…"
}

func rowLines(_ e: Entry) -> String {
  let bits = [e.app.isEmpty ? "" : "from \(e.app)", relTime(e.ts)].filter { !$0.isEmpty }
  // Main row: full text is searchable; length= keeps the DISPLAY compact. NOTE:
  // the row must NOT carry a `key=` — a keyEquivalent on the primary stops AppKit
  // from pairing/hiding the ⌥-alternate below, which would show two rows.
  var p = "length=60 bash=\(quote(helperPath)) param0=copy param1=\(e.id) param2=\(autopaste) sfimage=\(e.icon)"
  if !bits.isEmpty { p += " tooltip=\(quote(bits.joined(separator: "  ·  ")))" }
  var out = "\(e.text) | \(p)\n"
  // Option-key alternate: one visible row; hold ⌥ and it becomes Pin/Unpin.
  // Both halves are ordinary match candidates in the search panel while a
  // query is active — this isn't menu-only.
  if e.pinned {
    out += "Unpin “\(short(e.text))” | alternate=true bash=\(quote(helperPath)) param0=unpin param1=\(e.id) sfimage=pin.slash\n"
  } else {
    out += "Pin “\(short(e.text))” | alternate=true bash=\(quote(helperPath)) param0=pin param1=\(e.id) sfimage=pin\n"
  }
  return out
}

func render(_ history: [Entry], accessGranted: Bool) {
  let pinned = history.filter { $0.pinned }
  let recent = history.filter { !$0.pinned }
  var out = "\(history.count) | sfimage=doc.on.clipboard\n---\n"
  // macOS 15.4+ Pasteboard Privacy: if the user hasn't granted permanent
  // access, we stop polling (see pasteboardAccessGranted()) rather than
  // triggering a system prompt on every clipboard change. This banner is the
  // only way they'd know why history stopped updating.
  if !accessGranted {
    out += "Clipboard access needed | sfimage=exclamationmark.triangle.fill sfcolor=#F5A623 disabled=true\n"
    out += "Grant it once — Vee won't ask again | size=11 color=#8A8F98 disabled=true\n"
    out += "Open Privacy & Security Settings | href=x-apple.systempreferences:com.apple.preference.security sfimage=gear\n"
    out += "---\n"
  }
  if autopaste == "1" && !AXIsProcessTrusted() {
    out += "Autopaste needs Accessibility permission | sfimage=exclamationmark.triangle.fill sfcolor=#F5A623 disabled=true\n"
    out += "Grant in Privacy & Security → Accessibility | size=11 color=#8A8F98 disabled=true\n"
    out += "---\n"
  }
  if history.isEmpty {
    out += "No clipboard history yet | disabled=true\n"
  }
  if !pinned.isEmpty {
    out += "Pinned | header=true\n"  // a native section header ignores appearance params
    for e in pinned { out += rowLines(e) }
    if !recent.isEmpty { out += "---\n" }
  }
  for e in recent { out += rowLines(e) }
  out += "---\nClear history | searchable=false bash=\(quote(helperPath)) param0=clear sfimage=trash\n"
  // `~~~` AFTER the block so StreamAccumulator emits THIS render immediately —
  // with `~~~` first, the newest menu stays buffered until the next copy.
  out += "~~~\n"
  print(out, terminator: "")
}

// ── Pasteboard Privacy (macOS 15.4+) ──────────────────────────────────────────
// Reading `accessBehavior` is a permission-status query, not a pasteboard
// content read, so checking it never itself triggers the system alert. A
// clipboard manager that keeps calling pb.string(forType:) every poll while
// access is only "ask" gets the user re-prompted constantly; the well-behaved
// pattern is to stop reading until they flip it to Always Allow once.
func pasteboardAccessGranted() -> Bool {
  if #available(macOS 15.4, *) {
    return NSPasteboard.general.accessBehavior == .alwaysAllow
  }
  return true // no such gate on older macOS
}

// ── Capture ──────────────────────────────────────────────────────────────────
let pb = NSPasteboard.general
let concealed: Set<String> = [
  "org.nspasteboard.ConcealedType",     // password managers
  "org.nspasteboard.TransientType",     // transient (don't store)
  "org.nspasteboard.AutoGeneratedType", // machine-generated
]

func sweepOrphans(_ valid: Set<String>) {
  guard let files = try? fm.contentsOfDirectory(atPath: dir) else { return }
  for f in files {
    let id: String
    if f.hasSuffix(".txt") { id = String(f.dropLast(4)) }
    else if f.hasSuffix(".pin") { id = String(f.dropLast(4)) }
    else { continue }
    if !valid.contains(id) { try? fm.removeItem(atPath: dir + "/" + f) }
  }
}

var history = loadManifest()
sweepOrphans(Set(history.map { $0.id }))
var lastManifestMtime = mtime(manifestPath)

func addEntry(_ original: String, app: String) {
  let h = hash(original)
  let now = Int(Date().timeIntervalSince1970)
  if let idx = history.firstIndex(where: { $0.hash == h }) {
    var e = history.remove(at: idx)
    e.ts = now
    history.insert(e, at: 0)
  } else {
    let id = String(now * 1_000_000 + Int(arc4random_uniform(1_000_000)))
    let path = dir + "/\(id).txt"
    do {
      try original.write(toFile: path, atomically: true, encoding: .utf8)
      try fm.setAttributes([.posixPermissions: 0o600], ofItemAtPath: path) // belt-and-suspenders over the 0700 dir
    } catch {
      log("clipboard: write \(id).txt failed: \(error)")
      return
    }
    history.insert(Entry(id: id, hash: h, app: app, icon: iconFor(original),
                         text: oneLine(original), ts: now,
                         pinned: false), at: 0)
  }
  // Cap unpinned only — a pin is a deliberate user action, so pins are never
  // silently evicted by count (see VEE_CLIP_MAX's description).
  let unpinned = history.filter { !$0.pinned }
  if unpinned.count > maxItems {
    let evict = Set(unpinned.suffix(unpinned.count - maxItems).map { $0.id })
    for id in evict {
      do { try fm.removeItem(atPath: dir + "/\(id).txt") }
      catch { log("clipboard: evict \(id).txt failed: \(error)") }
    }
    history.removeAll { evict.contains($0.id) }
  }
  saveManifest(history)
  lastManifestMtime = mtime(manifestPath) // we wrote it — not an external change
}

func pruneExpired() -> Bool {
  let now = Int(Date().timeIntervalSince1970)
  let maxAgeSeconds = maxAgeDays > 0 ? maxAgeDays * 86400 : 0
  let toEvict = history.filter { e in
    guard !e.pinned else { return false }
    if maxAgeSeconds > 0 && (now - e.ts) >= maxAgeSeconds { return true }
    return false
  }
  guard !toEvict.isEmpty else { return false }
  for e in toEvict {
    do { try fm.removeItem(atPath: dir + "/\(e.id).txt") }
    catch { log("clipboard: prune \(e.id).txt failed: \(error)") }
  }
  let evictIDs = Set(toEvict.map { $0.id })
  history.removeAll { evictIDs.contains($0.id) }
  saveManifest(history)
  lastManifestMtime = mtime(manifestPath)
  return true
}

func consider(seed: Bool) {
  // Resolve the frontmost app and bail on the ignore list BEFORE touching any
  // pasteboard content — otherwise a secret copied in an ignored app (e.g.
  // 1Password) is still pulled into our process memory even though it's
  // never written to disk.
  let appName = seed ? "" : (NSWorkspace.shared.frontmostApplication?.localizedName ?? "")
  if !appName.isEmpty, ignoreApps.contains(appName.lowercased()) { return }
  let types = Set((pb.types ?? []).map { $0.rawValue })
  guard types.isDisjoint(with: concealed),
        let text = pb.string(forType: .string),
        !text.trimmingCharacters(in: .whitespacesAndNewlines).isEmpty,
        text.utf8.count <= maxBytes else { return }
  addEntry(text, app: appName)
}

var lastChange = pb.changeCount
var granted = pasteboardAccessGranted()
if granted { consider(seed: true) } // show whatever's already on the clipboard, if we're allowed to look
render(history, accessGranted: granted)

var lastAccessCheck = Date().timeIntervalSince1970
var lastPruneCheck = Date().timeIntervalSince1970

while true {
  let now = Date().timeIntervalSince1970
  if !granted {
    // Cheap permission re-check (no prompt) every few seconds so the menu
    // recovers the moment the user flips the Settings toggle.
    if now - lastAccessCheck >= 3 {
      lastAccessCheck = now
      let nowGranted = pasteboardAccessGranted()
      if nowGranted {
        granted = true
        lastChange = pb.changeCount // adopt current clipboard state; don't backfill history we missed
        render(history, accessGranted: true)
      }
    }
  } else if pb.changeCount != lastChange {
    lastChange = pb.changeCount
    consider(seed: false)
    render(history, accessGranted: true)
  }

  if now - lastPruneCheck >= 30 {
    lastPruneCheck = now
    if pruneExpired() { render(history, accessGranted: granted) }
  }

  let m = mtime(manifestPath)
  if m != lastManifestMtime { // external change (pin / unpin / clear)
    lastManifestMtime = m
    let before = history.count
    history = loadManifest()
    if history.count != before { saveManifest(history); lastManifestMtime = mtime(manifestPath) }
    render(history, accessGranted: granted)
  }
  usleep(500_000)
}
