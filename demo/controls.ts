#!/usr/bin/env node
//
// controls.ts — every menu-row control Vee's plugin format supports, in one
// file, printing the xbar/SwiftBar text protocol directly with plain
// console.log() calls — no dependency. Byte-identical output to controls.py.
//
// The point of this file is what a dependency-free plugin looks like: every
// `|`-param line below is `text | key=value key2=value2 …`, and YOU are
// responsible for two things:
//
//  1. Quote any param value containing whitespace, `|`, or `\` — see the
//     `param1=` and `tooltip=` lines below. Miss one and the value silently
//     truncates at the first space Vee's parser hits.
//  2. Escape a literal `|` or `\` inside DISPLAY TEXT (not a param value) as
//     `\|` / `\\`, or it's read as the params delimiter and corrupts the
//     line — see "Reserved chars" below. This bit a hand-formatted plugin in
//     this very repo once: `a|b` rendered as `a`, silently dropping `b and
//     back\slash` as if it were parameters.
//
// This is a reference, not a utility: every row is a static example, nothing
// reads real system state. The two interactive rows (toggle/slider) and the
// "runs a command" row shell out to /bin/echo, whose output goes nowhere —
// a harmless stand-in for a real command. href/webview only ever open this
// project's own GitHub page.
//
// <vee.title>Controls Demo (no SDK)</vee.title>
// <vee.version>1.0</vee.version>
// <vee.author>Naveen Kumar</vee.author>
// <vee.author.github>navbytes</vee.author.github>
// <vee.desc>Every control the Vee plugin format supports, hand-formatted with no SDK.</vee.desc>
// <vee.dependencies>Node 24+ (runs the .ts file directly via native TypeScript type-stripping)</vee.dependencies>
// <vee.abouturl>https://github.com/navbytes/vee-plugins</vee.abouturl>
// <vee.filter>true</vee.filter>
//
// Trust declarations (advisory, never enforced): the three rows described
// above are the only ones that run anything, and they only run on click.
// <vee.capabilities>exec</vee.capabilities>
// <vee.exec>echo</vee.exec>

// A 1x1 transparent PNG — stands in for real artwork so `image=`/
// `templateimage=` have something to render.
const PIXEL_PNG =
  "iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAYAAAAfFcSJAAAACklEQVR4nGNgAAACAAEA3Y0qPQAA" +
  "AABJRU5ErkJggg==";
const DOCS_URL = "https://github.com/navbytes/vee-plugins";

// --- Title (before the first "---"): Vee cycles/stacks multiple title lines.
console.log("Controls Demo | color=blue sfimage=slider.horizontal.3");
console.log("SDK | color=gray size=11");
console.log("---");

// ---------------------------------------------------------------------------
console.log("Text & rendering | header=true");
console.log("Colored text | color=purple");
console.log("Monospaced, 14pt | size=14 font=Menlo");
console.log("Truncated to 12 characters from a much longer string | length=12");
// The value itself carries the padding this row demonstrates trim= on — no
// quoting needed for the param (trim=true has no spaces), only for the text.
console.log("   trimmed of its own leading/trailing space    | trim=true");
console.log("\x1b[32mANSI is interpreted by default\x1b[0m");
console.log("\x1b[32mansi=false shows the raw escape codes\x1b[0m | ansi=false");
console.log("Emoji shortcode :smile: rendered via emojize=true | emojize=true");
console.log("**Bold** and _italic_ via md=true | md=true");
// Manual escaping: a bare "|" would truncate this line at "a", and a bare
// "\" would be read as an escape prefix for whatever follows it. Order
// matters — escape backslashes first, or the backslash this adds for "|"
// gets re-escaped a second time.
console.log("Reserved chars: a\\|b and a back\\\\slash, escaped automatically");
console.log("---");

// ---------------------------------------------------------------------------
console.log("Click behavior | header=true");
console.log(`Open the docs | href=${DOCS_URL}`);
// param1= and tooltip= both contain spaces, so both need quotes. A value's
// own double quotes would need \" — none do here.
console.log(
  'Run a harmless command | shell=/bin/echo param1="hello from the SDK demo" ' +
    'terminal=false tooltip="Runs /bin/echo in the background; output goes nowhere"'
);
console.log("Refresh this menu | refresh=true");
console.log("Hold Option to see the alternate below");
console.log("Option: this row replaces the one above it | alternate=true");
console.log("Disabled row — greyed out, not clickable | disabled=true");
console.log("Has a keyboard shortcut while the menu is open | key=cmd+shift+d");
// "Say Hello" has a space, so it's quoted; cmd+shift+d above has none, so it
// isn't — the rule is per-value, not per-parameter.
console.log('Runs a named macOS Shortcut (only if one exists with this name) | shortcut="Say Hello"');
console.log(`Opens a WebView window | webview=${DOCS_URL} webvieww=480 webviewh=360`);
console.log("---");

// ---------------------------------------------------------------------------
console.log("Placement & visibility | header=true");
console.log(
  "Its accessory is anchored to the leading edge | " +
    "accessory=leading progress=0.4 accessoryw=100 accessoryh=8"
);
console.log(
  'Only visible in the menu and a detached window | ' +
    'tooltip="Absent from the search panel and vee search" visibleon=menu,window'
);
console.log(
  'searchable=false — browsable, never search-matched | ' +
    'tooltip="A typed query plus Return can never land on this row" searchable=false'
);
console.log("---");

// ---------------------------------------------------------------------------
console.log("Images | header=true");
console.log(`image= (base64 PNG) | image=${PIXEL_PNG}`);
console.log(`template_image= (adapts to light/dark) | templateimage=${PIXEL_PNG}`);
console.log("---");

// ---------------------------------------------------------------------------
console.log("SF Symbols | header=true");
console.log("sfimage= | sfimage=cpu");
console.log("sfimage= + sf_color= | sfimage=battery.100 sfcolor=green");
console.log(
  "sfimage= + multicolor sf_color list + sf_size= | " +
    "sfimage=person.crop.circle.badge.checkmark sfcolor=blue,green sfsize=18"
);
// sfconfig's embedded double quotes stay bare and unescaped: the whole value
// has no whitespace/pipe/backslash, so the quoting rule never triggers.
console.log('sfimage= + sf_config= | sfimage=bolt.fill sfconfig={"scale":"large","weight":"bold"}');
console.log("Inline glyph: status :checkmark.circle: via symbolize=true | symbolize=true");
console.log("---");

// ---------------------------------------------------------------------------
console.log("Annotations | header=true");
console.log('Hover for a tooltip | tooltip="This is the tooltip text"');
console.log("Checked row | checked=true");
console.log("Inbox | badge=12");
console.log("---");

// ---------------------------------------------------------------------------
console.log("Charts & gauges | header=true");
console.log(
  "Load history | sparkline=1,2,3,5,8,13,8,5,3,2 sparklinecolor=teal " +
    "accessoryw=140 accessoryh=20"
);
console.log(
  "Disk usage (fraction) | color=green progress=0.72 " +
    "progresstrackcolor=#333333 accessoryw=120 accessoryh=8"
);
// progress=value,max — Vee divides these on parse, same grammar as slider=.
console.log("Storage (value,max) | color=orange progress=23.65,100");
console.log("By category (pie) | pie=45,30,25 chartlabels=Documents,Photos,Apps");
// "Macintosh HD" has a space, so the WHOLE comma list is quoted — a chart
// label can't itself contain a comma, but it can contain spaces.
console.log(
  'By volume (donut) | donut=512,256,128 chartlabels="Macintosh HD,Backup,Scratch" ' +
    "chartcolors=blue,teal,orange"
);
console.log(
  "Budget (stacked bar, full width) | stackedbar=60,25,15 " +
    "chartlabels=Used,Cache,Free accessoryw=full accessoryh=14"
);
console.log("---");

// ---------------------------------------------------------------------------
console.log("Interactive controls | header=true");
console.log(
  'Notifications | shell=/bin/echo param1="toggle ->" refresh=true ' +
    "searchable=false toggle=on"
);
console.log(
  'Volume | shell=/bin/echo param1="slider ->" refresh=true ' +
    "searchable=false slider=0,100,40 accessoryw=120"
);
console.log("---");

// ---------------------------------------------------------------------------
// "--" nests one level, "----" nests two — depth is however many "--" pairs
// prefix the line, no closing marker needed.
console.log("Nesting | header=true");
console.log("Recent | sfimage=clock");
console.log("--This week");
console.log("----#4210 passed | color=green");
console.log("----#4209 failed | color=red");
console.log("--Last week | color=gray");
console.log("---");
console.log("Refresh | refresh=true sfimage=arrow.clockwise");
