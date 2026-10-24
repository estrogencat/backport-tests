# discord -> ios 15.7.7: patch pipeline prototype

**target: 350.0** (CFBundleVersion 115636). 349.0 was the first one through and
its numbers are kept below as the baseline; the 350.0 section at the end is the
live one.

status: **it runs.** discord 350.0 launches and stays up on the test device,
15.7.7 / 19H357 -- through the app delegate, the run loop, the javascript
runtime, react native's turbomodule setup and into the ui, with images
loading. all four binaries
report 0 strong-missing symbols against the real 15.7.7 dyld shared cache.
the history below is kept in order, so the earliest sections still describe the
load-time work as if nothing had been run yet; "it runs" at the end is the
current state.

## how to run it

    ./run.sh          # unpack, patch, build the shim, verify, package an ipa
    ./install.sh      # upload and install over trollstore
    ./launch.sh       # launch on the phone, say whether it stayed up
    ./crash_last.sh   # pull the newest crash report and symbolize it
    ./dbg.sh probe_trace.py     # debugserver + a probe against the live app

the next release is one line:

    VER=351 IPA=/path/to/new.ipa PREV=350 ./run.sh

`env.sh` holds the phone, the key, the shared cache path and the target
release. everything generated goes under `out/<VER>/` and is entirely
regenerable -- the unpacked ipa, the export index, the shim, the patched bundle
and the ipa -- so `out/` can be deleted at will. what is worth keeping is small:
the scripts, this file, `syms/<VER>/` (the per-binary shim symbol lists, which
are what you diff between releases) and `crashes/`. with `PREV` set, `run.sh`
ends by printing the shim-surface diff against that release.

the 15.7.7 shared cache is not included; `DSC` in the environment points at
an extracted copy.

**the `probe_*.py` scripts hardcode shared-cache addresses** (CoreFoundation's
`doesNotRecognizeSelector:`, `UIApplicationMain`) taken from a crash report.
the cache slide is fixed at boot, so those stay valid until the phone reboots
and must be re-read from a fresh `.ips` after one. `gdbr.py` is the
gdb-remote client from the spotify project.

## result (349.0, the baseline run)

verified with `verify.py`, whose export index is the 15.7.7 cache (2406 images,
re-export chains followed) plus the shim's own export trie plus the four bundled
frameworks' export tries -- so no import sits in a bucket that is merely skipped:

| binary | ok | flat-lookup | weak-missing | **strong missing** |
|---|---|---|---|---|
| Discord | 9931 | 954 | 364 | **0** |
| NotificationService | 1065 | - | 16 | **0** |
| Share | 1423 | - | 26 | **0** |
| BroadcastUpload | 2148 | 8 | 6 | **0** |

before patching those same four were 567 / 153 / 99 / 67 strong-missing.

the two non-zero columns are normal, not debt: 314.2 -- which does run on
iOS 15 -- comes out of the same tool at 504 flat-lookup and 243 weak-missing
with 0 strong-missing. flat-lookup is chained-fixups ordinal 0xfd
(`-undefined dynamic_lookup`); weak-missing binds to null and the code checks.

minos is 15.0 in every `LC_BUILD_VERSION` and `MinimumOSVersion` is 15.0 in all
four Info.plists. `LockscreenWidget.appex` is gone (it wanted 16.2 and never
loaded on 15 in 314.2 either); nothing in the main Info.plist referenced it.

### one finding not in the earlier report

the four bundled frameworks needed checking too, since a launch failure in
them is still a launch failure. `RiveRuntime`, `YbridOgg`, `YbridOpus` and
`hermesvm` are all clean on iOS 15 (minos 14.0/14.0/14.0/15.1, 0 strong-missing)
so none of them needs patching. the first pass appeared to show RiveRuntime
missing `_OBJC_CLASS_$_MTKView`; that was a gap in the export index -- MetalKit
is in the 15 cache but was not being collected because none of the four main
binaries links it.

## what each edit is

**1. import rewriter (`patch.py`)** -- 567/153/99/67 imports repointed. for each
strong import whose own library does not export it on iOS 15, the library
ordinal is rewritten in *both* places that carry one: the
`DYLD_CHAINED_FIXUPS` imports array (format 1, 8-bit ordinal) and the matching
`LC_SYMTAB` `n_desc`. 441 Foundation imports go to a new
`/usr/lib/swift/libswiftFoundation.dylib`, 6 libswiftCoreFoundation ones to the
`libswiftCoreGraphics.dylib` the binary already loads, the rest to the shim.

no mach-o rebuild was needed. the padding between the header and the first
section is 117 KB in Discord and 41-57 KB in the appex, so the new
`LC_LOAD_DYLIB`s fit in place and *nothing in the file moves* -- no offset, no
fixup chain and no linkedit table is rewritten. new load commands go at the end
of the list so existing ordinals keep their meaning.

**also required, and not in the original plan:** `AppIntents` and
`libswift_StringProcessing.dylib` do not exist on iOS 15 at all and were loaded
with a plain `LC_LOAD_DYLIB`, which makes dyld refuse the image outright no
matter what the imports say. both are now `LC_LOAD_WEAK_DYLIB`. that is only
safe because every *strong* import of those two was repointed at the shim; the
weak ones bind to null as intended.

**2. shim dylib (`mkshim.py`)** -- `libdiscordshim.dylib`, 61528 bytes, arm64,
minos 15.0, install name `@rpath/libdiscordshim.dylib` (all four binaries
already have the matching `LC_RPATH`s). emitted byte by byte: no Apple
toolchain is involved, there isn't one on this machine. 4 segments, classic
`LC_DYLD_INFO_ONLY` (rebase/bind/export-trie -- iOS 15 dyld handles it fine and
it is far simpler than chained fixups to emit), `LC_SYMTAB` + `LC_DYSYMTAB`,
24 rebases, 25 binds, 5 imports from libobjc. 124 exports:

| | n | how |
|---|---|---|
| `_objc_retain_xN` / `_objc_release_xN` | 36 | real stubs: save x0-x18 and q0-q7, move xN to x0, call libobjc's `objc_retain`/`objc_release` through the shim's own `__got`, restore everything, put the result back in xN. these should behave correctly, not just link. |
| `_OBJC_CLASS_$_*` | 4 | inert objc class pairs (superclass NSObject, no methods, no ivars), with `__objc_classlist` and `__objc_imageinfo` so the runtime realizes them |
| swift metadata (`Mn Mp Mc MQ Tq Tb Tl Tn WC`) | 36 | 64 zero bytes each |
| swift async function pointers (`Tu`) | 2 | `{relative ptr -> trap, context size}` |
| everything else | 46 | one instruction, `brk #N` |

`checkshim.py` re-reads the result independently: segment/section containment,
16 KB alignment, dyld-info streams in range and every rebase/bind offset inside
its segment, export trie round-trips to exactly the 124 wanted names, every
defined symbol inside a real section. it passes.

the 46 `brk` stubs are deliberate. `libdiscordshim.dylib.map` maps each
`brk #N` back to its symbol, so the **first** thing a real run tells you is
which stub gets reached first and from where, instead of a silent `ret`
returning garbage into Swift and crashing somewhere unrelated three frames
later. `--mode ret` builds the silent version if you want to get past one.

## step 4 (AppIntents, swift Regex): what it really needs

right now step 4 is satisfied *for the linker only*, which was the point of the
milestone. the gap is the Swift runtime, not dyld:

- **AppIntents, 38 symbols.** 27 are metadata descriptors now served as 64 zero
  bytes, 11 are accessors/functions served as traps. registration survives that -- the conformance table is keyed on the
  protocol descriptor *pointer* and does not dereference it -- but the first
  conformance lookup or metadata instantiation for the intent type walks a
  zeroed `ProtocolDescriptor` and dies. the cheap fix is not to write better
  metadata, it is to **stop the type being registered at all**: find the
  intent's records in Discord's own `__swift5_types` / `__swift5_proto` and zero
  them (shrinking the section size), after which link-satisfiable stubs are
  genuinely enough. writing a real iOS-15-ABI `AppIntent` protocol descriptor by
  hand is the alternative and is both fiddly and locked to one stdlib version.
- **swift Regex, 6 symbols** (3 metadata, 3 code). same shape, worse. the call sites are generic over
  `RegexComponent`, so there is no single symbol to neuter -- the conformance
  `_$s17_StringProcessing5RegexVyxGAA0C9ComponentAAMc` is itself one of the
  zeroed descriptors. realistically either patch the two or three call sites
  over to the `NSRegularExpression` path, or build `_StringProcessing` from
  swift-experimental-string-processing, where the original report's warning
  stands: it reaches for libswiftCore internals iOS 15's stdlib does not export,
  and you cannot ship your own libswiftCore beside the OS overlay.
- the 15 Foundation/Locale/URL getters, 10 StoreKit 2 and 9 Concurrency symbols
  are ordinary functions. they are cheap to implement for real later
  (`URL.documentsDirectory` and friends are a few lines each); they are traps
  today only because nothing has needed them yet.

## signing

re-signing is required: adding load commands invalidates the existing signature
on purpose. nothing here signs anything, because **trollstore does it** --
`trollstorehelper install` runs ldid over the app, every `.appex` and the shim,
then applies the CoreTrust bypass to each. 15.7.7 (19H357) is *not* past that
window, contrary to what the first draft of this note said; trollstore is
installed on the phone and works. so no 7-day certs and no AltStore.

the one wrinkle: trollstore refuses to replace an app it did not install
("already installed and not a TrollStore app... bailing out"), so an App Store
discord has to be deleted first.

## files

drivers first, then the python:

| | |
|---|---|
| `env.sh` | phone, key, shared-cache path, target release, output paths |
| `run.sh` | unpack -> patch -> shim -> verify -> ipa, one release per run |
| `install.sh`, `launch.sh`, `crash_last.sh`, `dbg.sh` | the device loop |

| | |
|---|---|
| `mo.py` | mach-o load commands, segments, header-slack accounting, load-command append |
| `cfix.py` | `DYLD_CHAINED_FIXUPS` imports array and `LC_SYMTAB` ordinal read/write |
| `a64.py`, `trie.py` | arm64 encoders; dyld export-trie builder (round-trip tested) |
| `collect15.py` | export index from the 15.7.7 cache, re-exports followed |
| `plan.py` | per-import verdict: ok / retarget / shim / weak-missing / flat-lookup |
| `mkshim.py` | emits `libdiscordshim.dylib` + `.map` |
| `checkshim.py` | structural self-check of the emitted dylib |
| `patch.py` | the three in-place binary edits |
| `build.py` | copy bundle, drop the widget, patch, install shim, fix plists |
| `verify.py` | the zero-strong-missing check |
| `out/<VER>/bundle/Payload/Discord.app` | the patched, unsigned bundle |

## 350.0 (CFBundleVersion 115636), run through the same pipeline unchanged

the whole thing re-ran with no code changes: **0 strong-missing on all four
binaries** again.

| binary | before | after | imports repointed |
|---|---|---|---|
| Discord | 576 | **0** | 576 |
| NotificationService | 153 | **0** | 153 |
| Share | 110 | **0** | 110 |
| BroadcastUpload | 67 | **0** | 67 |

same shape as 349.0: arm64 subtype 0, cryptid 0, minos 16.0 (widget 16.2),
107/30/37/45 dylibs, 117 KB of header padding in Discord, and the same 13
frameworks absent from the 15.7.7 cache. the bundled frameworks are still clean
(RiveRuntime's export table has been stripped from 8669 to 1546 symbols, but its
*imports* resolve).

the shim surface moved by 10 symbols: 128 vs 124. **seven new**

    _$s2os21OSAllocatedUnfairLockVMn                      os, ios 16
    _$ss15ContinuousClockV7InstantVs0C8ProtocolsMc        InstantProtocol conformance
    _$ss15InstantProtocolP8advanced2byx8DurationQz_tFTj   dispatch thunk
    _$ss5ClockP3now7InstantQzvgTj                         dispatch thunk
    _$ss5ClockP5sleep5until9tolerancey...FTj  (+ its Tu)  dispatch thunk
    _$ss8DurationV7secondsyABSdFZ

**three dropped** (`URL.DirectoryHint`, `URL.appending(component:)`). nothing
structurally new -- more of the same swift 5.7 Clock/Duration surface, plus a
mangling the classifier had not seen (`Tj`, protocol dispatch thunk), which
lands in the right bucket already since a thunk is code.

so the per-release cost looks like what the earlier estimate assumed: the
pipeline runs unattended and the delta is a handful of symbols in families that
already have a stub strategy. what it does *not* tell you is whether a new
unguarded ios-16 call site appeared, which only a run on the phone shows.

built at `out/350/bundle/Payload/Discord.app`. 349.0's symbol lists are kept
in `syms/349/` for the release diff; its bundle was not carried over, since
`run.sh` rebuilds any release from its ipa.

## on the device: it loads

installed over trollstore (`trollstorehelper install`, no force needed once the
app-store copy was gone) and launched on the test device, 15.7.7 / 19H357.

**dyld accepts the patched image.** no missing-symbol abort, no dyld error at
all: the process starts, the 447 repointed Foundation imports resolve against
`libswiftFoundation.dylib`, the shim loads and binds, and execution reaches
`UIApplicationMain` and the app delegate. trollstore's ldid signed the
hand-built shim without complaint and applied the CoreTrust bypass to it like
any other binary, so the mach-o is well-formed by its reckoning too.

that is the milestone answered: **the load-time surface was the easy part, and
it is done.** what follows is runtime.

### bug found and fixed: the objc_retain_xN ABI

first launch died in `application:didFinishLaunchingWithOptions:` with
`-[DCDFrameRateTracker startHangTracking]` sent to **NSNotificationCenter**.
traced on-device with debugserver (dyld image list for the slide, breakpoints
on the individual instructions):

    after metadata accessor   x0 = DCDFrameRateTracker        (right)
    after objc_allocWithZone  x0 = fresh DCDFrameRateTracker  (right)
    after [x init]            x0 = NSNotificationCenter       (wrong)

`-init`'s swift body keeps `self` in x19 and ends `mov x0, x19`. at
image+0xcd4398 it does `bl _objc_retain_x19` and then **`mov x19, x0`** -- so
the real `objc_retain_xN` takes its argument in xN and **returns the object in
x0**, the ordinary return register. my stub had it backwards: it preserved x0
and wrote the result into xN, so `mov x19, x0` picked up the stale x0, which
was the NSNotificationCenter class left over from a `defaultCenter` call eight
instructions earlier. that propagated into the `shared` singleton global and
blew up two calls later.

the fix is for retain to not restore x0. `objc_release_xN` returns nothing and
does preserve every register, which the on-device measurement confirmed, so it
is unchanged. `shimverify.py` now asserts the contract per stub -- that each
`_objc_(retain|release)_xN` really moves xN into x0, and that only the release
half restores x0 -- across all 36.

worth keeping in mind for the next one of these: a wrong register ABI in a stub
does not crash, it silently returns the wrong object. nothing static would have
caught it; it took a breakpoint per instruction.

### where it stands now

with that fixed, launch gets **through the app delegate and into the main run
loop** -- `UIApplicationMain` -> `-[UIApplication _run]` -> `CFRunLoopRun` ->
the main dispatch queue -> an `NSBlockOperation`. it dies there, at
image+0xfae2e8:

    x0 = [UIWindowSceneGeometryPreferencesIOS alloc]      (shim stand-in)
    bl "initWithInterfaceOrientations:"                   <-- unrecognized
    bl "requestGeometryUpdateWithPreferences:errorHandler:"

this one is not a patch bug, it is the first **genuine ios-16 api gap** at
runtime, and it is exactly the limit the inert stand-ins were documented to
have: the class exists so the link resolves, but it has no methods.

note the next line, though: `requestGeometryUpdateWithPreferences:errorHandler:`
goes to a **real UIWindowScene**, which ios 15 does not have either. so adding
methods to the stand-in only moves the failure one call along. this call site --
discord's ios-16 orientation-request path -- has to be neutered rather than
stubbed, and that is a different kind of work from everything above: patching a
call site in discord's own code instead of supplying a symbol.

### what the next step actually is

1. neuter the geometry-update block: the cheapest version is to make the whole
   enclosing function return early, since on ios 15 the orientation work has to
   go through `supportedInterfaceOrientations` anyway.
2. expect more of these. the earlier report predicted "a handful of unguarded
   ios-16 paths may still crash at first use" from the selector scan, and named
   this family. the trap-mode shim plus `callsite.py` turns each one into a
   twenty-minute loop: crash report -> image offset -> selector -> decide stub
   or neuter.
3. the swift-metadata question (AppIntents, Regex) has still not been reached.
   nothing has touched a zeroed descriptor yet, which is mildly encouraging for
   the "zero the `__swift5_types` records" plan over writing real metadata.

### tooling added for the on-device part

| | |
|---|---|
| `mkipa.py` | zip a bundle to .ipa keeping the executable bits |
| `ips.py` | parse .ips crash reports, symbolize against `usedImages` |
| `callsite.py` | disassemble around a crash offset; names objc selectors, cfstrings and classrefs through the chained-fixup slots |
| `objcls.py`, `metameth.py` | objc class / metaclass method lists and imps, from the file |
| `chains.py` | walk DYLD_CHAINED_FIXUPS; say which import any pointer slot binds to |
| `shimverify.py` | assert the retain/release stub register contract |
| `probe_*.py` | debugserver probes (gdbr.py from the spotify project): image list, breakpoint traces, register watches |

## neutering the ios-16 orientation call (`neuter.py`)

the geometry-request block is gone. `build.py` now runs `neuter.py` on the
patched `Discord` binary, between `patch.py` and installing the shim, so
`./run.sh` does it for every release.

what it edits: **one 4-byte word.** the block invoke function at
`image+0xfae258` gets a bare `ret` written over its prologue
(`sub sp, sp, #0x50`, `0xd10143ff`). the whole block body is ios 16 --
`UIWindowSceneGeometryPreferencesIOS`,
`-[UIWindowScene requestGeometryUpdateWithPreferences:errorHandler:]` and
`-[UIViewController setNeedsUpdateOfSupportedInterfaceOrientations]` -- so
there is nothing in it to keep, and on ios 15 doing nothing is correct:
orientation goes through `supportedInterfaceOrientations`. the block returns
void and owns none of the objects it touches, so an immediate return leaks
nothing.

**located by pattern, not by offset.** nothing in `neuter.py` hardcodes an
address. the chain is: chained-fixup bind for
`_OBJC_CLASS_$_UIWindowSceneGeometryPreferencesIOS` -> the `__objc_methname`
string for each selector -> its `__objc_selrefs` slot -> the `__objc_stubs`
entry that loads that slot (uniform 0x20-byte entries, adrp x1 / ldr x1 / adrp
x16 / ldr x16 / br x16) -> every `bl` to that stub in `__text` ->
`LC_FUNCTION_STARTS` for the enclosing function's bounds.

`initWithInterfaceOrientations:` has **two** senders in 350.0
(`0x1002be6fc` and `0x100fae2e8`), so the selector alone does not identify the
site. the discriminator is a three-part signature on the enclosing function:
it loads that classref, it sends
`requestGeometryUpdateWithPreferences:errorHandler:`, and its start address is
materialised exactly once by an `adrp`/`add` pair that is immediately stored
and followed within 24 instructions by `addOperationWithBlock:`. that last
check is not just disambiguation -- it is the proof the function is a void
block invoke, which is what makes an early `ret` safe rather than a function
returning garbage in x0.

every count is asserted to be exactly one and raises otherwise, so a release
that moves or restructures this code fails loudly. re-running is a no-op
(`already neutered`). `neuter.py` does its own arm64 decoding rather than using
capstone, because `build.py` runs under `python -I`, which hides user
site-packages.

    python -I neuter.py out/350/bundle/Payload/Discord.app/Discord --dry-run

after the edit, all four binaries still report **0 strong-missing**
(Discord 9968 ok / 941 special / 364 weak-missing), `checkshim.py` is `ok` and
`shimverify.py` is 36 stubs / 0 mismatched. the ipa was repacked.

## real implementations in the shim, and how far it now gets

the neuter got past the orientation block and launch then ran into the trap
stubs one at a time. three are now implemented for real, and the loop
(`./run.sh` -> `./install.sh` -> `./launch.sh` -> `./crash_last.sh` ->
`callsite.py`) turned each one around in minutes.

`mkshim.py` grew a **`real`** symbol category beside `code`/`retrel`/`meta`:
a hand-written body instead of a `brk`. supporting it needed three additions,
all of which are reusable for the next one:

- **more imports**: `_objc_msgSend` and `_OBJC_CLASS_$_NSFileManager`
  (Foundation, ordinal 3) and one swift entry point from
  `/usr/lib/swift/libswiftFoundation.dylib` (ordinal 4). all three are
  confirmed present in the 15.7.7 cache.
- **selectors**: a `__TEXT,__objc_methname` cstring section and a
  `__DATA,__objc_selrefs` pointer section (flags `0x10000005`), one rebase per
  slot. the objc runtime uniques them into real SELs at load like any other
  objc image -- the shim already carries `__objc_imageinfo` and
  `__objc_classlist`, so it is processed as one.
- **derived section indices**: the symtab section numbers used to be
  hardcoded (`objcclass: 7`, `meta: 8`). adding sections shifts them, so they
  now come from a single `SECTIONS` list that `layout()` asserts the emitted
  section order against.

### 1. the four URL directory getters

`URL.cachesDirectory` / `.libraryDirectory` / `.documentsDirectory` /
`.applicationSupportDirectory` (ios 16). react native asks for the caches
directory during startup, so this one was unavoidable.

`URL` is a **resilient** struct: callers do not know its size, so the public
abi returns it *indirectly* -- the caller passes a result buffer in x8 and the
getter returns void. discord does exactly that (`mov x8, x20` right before the
call), so the value cannot be synthesised, it has to be built by code that
knows the ios-15 layout. ios 15's overlay exports one:

    _$s10Foundation3URLV36_unconditionallyBridgeFromObjectiveCyACSo5NSURLCSgFZ

which takes an `NSURL?` in x0 and the same x8 result buffer (`mov x19, x8` on
entry). so each getter is

    [[NSFileManager defaultManager] URLsForDirectory:<dir>
                                         inDomains:NSUserDomainMask].firstObject
    -> URL._unconditionallyBridgeFromObjectiveC(that)

with x8 spilled across the three msgSends and reloaded for the bridge call, and
`objc_msgSend` reloaded from `__got` each time because it clobbers x16/x17.
`firstObject` is +0 and the bridge takes its argument at +0, so no retain.

### 2. CGPathRef.subtracting(_:using:)

path boolean ops are ios 16 and ios 15's CoreGraphics has no equivalent, so
this one cannot be implemented, only answered. it returns **the receiver,
retained**: "A minus B" answers A, which for the mask discord is building
means it hides nothing rather than hiding the wrong thing.

the register abi here is the interesting part and it was **measured, not
inferred** (`probe_cgpath.py`: no hardcoded shared-cache address needed, the
`brk` is its own breakpoint):

    x0  = 0x281ef50e0  isa -> __NSCFType     the *other* path
    x1  = 0x16f976290  == sp                 &rule
    x20 = 0x2802b5c80  isa -> __NSCFType     self
    lr  = image+0x9a265c                     the call site

so self is in **x20**, swift's self register -- and the reason x0 is not self
is that `CGPathFillRule` is a swift-native enum in a library-evolution
overlay, i.e. resilient, so it goes **by address**. ios 15's own
`CGPathRef.contains(_:using:transform:)` has the same shape and confirms it
statically (`ldrb w21, [x0]`, a byte read through an argument pointer).

reading x0 instead of x20 would have returned the wrong path without
trapping, which is the same failure mode as the `objc_retain_xN` bug. so
`shimverify.py` now asserts both new contracts per stub: that the URL getters
spill x8 before the first call and restore it before the bridge call, and that
the ret-self stubs move **x20** into x0.

### a real bug this turned up: no header slack for ldid

trollstore signs with ldid, which **appends a 16-byte `LC_CODE_SIGNATURE` to
the load-command list in place and moves nothing**. the shim put `__text`
immediately after its load commands, so signing overwrote the first
instructions of `__text` -- the first four `brk` stubs in the 349/350 layout.
they executed load-command bytes instead of trapping, which is silent.

fixed: `SIGN_SLACK = 32` bytes are reserved before `__text`, and
`checkshim.py` now fails if the gap after the load commands is under 16 bytes.
it reports `header slack 40 bytes ok`.

### where it stands now

| binary | ok | special | weak-missing | **strong missing** |
|---|---|---|---|---|
| Discord | 9968 | 941 | 364 | **0** |
| NotificationService | 1077 | - | 16 | **0** |
| Share | 1465 | - | 26 | **0** |
| BroadcastUpload | 2146 | 8 | 6 | **0** |

`checkshim.py` ok, `shimverify.py` 36 retain/release + 4 URL getters + 1
ret-self, 0 mismatched. shim is 128 exports: 43 trap stubs left, 5 real.

launch now gets through the app delegate, the main run loop, the javascript
runtime starting up, and into **react native's turbomodule setup**, where it
stops reproducibly at `image+0xccee24`:

    mov  x0, #0
    bl   "type metadata accessor for ContinuousClock.Instant"   <-- brk
    mov  x28, x0
    ldur x19, [x0, #-8]        ; the value witness table
    ldr  x22, [x19, #0x40]     ; VWT + 0x40 = the type's *size*
    mov  x8, sp                ; ... which is then used to allocate for it

the surrounding code clamps a double to [0, 60000] first, so this is a
millisecond timeout going through `Task.sleep(until:tolerance:clock:)`.

**this one needs real swift metadata, not a stub** -- and unlike AppIntents it
is not avoidable by suppressing a type registration, because the call site
dereferences the metadata immediately to read the size out of its value
witness table. zeroed metadata would read size 0. it is also the most
tractable instance of that problem in the whole surface:
`ContinuousClock.Instant` wraps a `Swift.Duration`, which is two 64-bit words,
so the type is a 16-byte POD -- a struct metadata record plus a value witness
table whose copy/take witnesses are a 16-byte move and whose destroy is a
`ret`. the other ten symbols in the Clock/Duration/InstantProtocol family
(`now`, `advanced(by:)`, `<`, `Duration.seconds`, `.milliseconds`, and the
protocol dispatch thunks) are ordinary code once the metadata exists.

### two operational notes

- **launch twice after installing.** the first launch after
  `trollstorehelper install` once trapped at a shim offset that is inside the
  load commands of the shim actually on disk, i.e. it ran against a stale
  mapping of the previous bundle. every launch after that trapped at correct,
  explainable offsets. so a crash on the very first launch after an install is
  worth re-checking before believing it.
- **the remaining traps are not a single chain.** different threads reach
  different ones depending on timing: the StoreKit 2
  `Product.priceFormatStyle.getter` trap won the race once, on
  `com.apple.root.user-initiated-qos.cooperative`, while three consecutive
  launches all stopped at `ContinuousClock.Instant` on the turbomodule queue.
  so the frontier is a set, and the count of remaining work is better read off
  the 43 trap stubs than off one crash at a time.

## real swift metadata: the Clock / Duration family (`swiftclock.py`)

the trap after the URL getters and `CGPathRef.subtracting` was the swift 5.7
clock surface, reached from react native's turbomodule setup. discord builds an
image-prefetch deadline:

    ContinuousClock.now.advanced(by: .milliseconds(x))     x clamped to 0..60000

this one could not be stubbed at all, and the call site says why:

    bl   "type metadata accessor for ContinuousClock.Instant"
    ldur x19, [x0, #-8]        ; the value witness table
    ldr  x22, [x19, #0x40]     ; VWT + 0x40 = the type's size
    mov  x8, sp                ; ...and then allocates that much

`ContinuousClock.Instant` is **resilient** -- the stdlib is built with library
evolution and `Instant` is not `@frozen` -- so callers do not know its size and
ask the metadata. zeroed metadata (the shim's `meta` category) reads size 0.
this is the "write real metadata by hand" option the AppIntents note called
fiddly, and it turned out to be the tractable instance of it.

### what is emitted

`swiftclock.py` emits, for both `ContinuousClock.Instant` and
`ContinuousClock`: a struct metadata record, a value witness table, and a
nominal type descriptor, plus real code for nine symbols.

the layout was not guessed. `Swift.Int`'s own metadata was read out of the
15.7.7 cache first: kind word `0x200`, VWT pointer at metadata-8, size at
VWT+0x40, stride at +0x48, flags `0x7` for an 8-aligned POD, extra
inhabitants 0. `Int` also has **zero extra inhabitants**, exactly like these
do, so its two enum-tag witnesses were disassembled and used as the reference
implementation: tag byte one past the payload, case index in the payload.

being resilient is also a licence -- nothing in discord's code knows the
layout -- so `Instant` is defined here as what the stdlib uses anyway: a
`Swift.Duration`, a 128-bit signed attosecond count, `_low` then `_high`.
`Duration` itself *is* `@frozen`, so its layout is not ours to choose and it is
returned directly in x0/x1.

| symbol | implementation |
|---|---|
| `Instant` / `ContinuousClock` metadata accessors | return the static record, state 0 |
| `Instant`'s VWT | 16-byte copy/take, `ret` destroy, `Int`-style enum tags |
| `ContinuousClock.now` | `clock_gettime(CLOCK_MONOTONIC_RAW)` -> attoseconds |
| `Instant.advanced(by:)` | 128-bit `adds`/`adc` |
| `Instant.<` | 128-bit signed `subs`/`sbcs`/`cset lt` |
| `Duration.milliseconds` / `.seconds` | scale the double to whole ns, then a 128-bit `mul`/`smulh` to attoseconds |
| `Clock.continuous` | `ret` -- an empty struct |
| `...InstantVMn` | the nominal type descriptor, exported |

the register abi was read off discord's own call site rather than assumed:
the metadata accessor takes the request in x0 and returns metadata in x0 with
state in x1; `now` returns **indirectly** through a caller-provided x8 buffer;
`advanced(by:)` takes x8 = result, x20 = `&self`, x0/x1 = the `Duration`.

`a64.py` gained about twenty encoders for this (`movk`, `umulh`/`smulh`,
`adds`/`adc`/`sbcs`, `cset`, `fmov Dd,Xn`, `fcvtzs`, `cbz`, `ldrb`/`strb`...),
each spot-checked by disassembling the generated bytes back with capstone.

### `clockverify.py`, and the bug it was written for

hand-built metadata fails the way hand-built stubs do: quietly. so
`clockverify.py` re-derives all of it from the emitted file -- follow the
accessor's own instructions to the record, the record to its VWT and
descriptor, the descriptor's relative pointers back to the names and to the
accessor they came from -- and `run.sh` runs it.

reading the generated code back caught a real bug before it ever ran: in
`storeEnumTagSinglePayload` the "payload case" branch was computed one
instruction short and landed on a bare `ret` instead of the `strb wzr` that
clears the tag byte. the effect would have been a stale non-zero tag, so a
later read of an `Optional<Instant>` would report an empty case instead of the
value -- no crash, just a wrong answer. `clockverify.py` now asserts that that
branch lands on a `strb wzr`.

## the async abi: Task.sleep(until:tolerance:clock:)

with the clock in place the next stop was the `sleep` that consumes the
deadline -- an **async throws** function, which needs the async calling
convention rather than a stub. all three parts of it were established
empirically:

- **the context is in x22.** ios 15's `Task.sleep(nanoseconds:)` does
  `str x22, [sp, #0x38]` on entry and keeps its state at `[x22, #0x60]`.
- **a function completes by branching to its own `ResumeParent` at
  `[x22, #8]`, leaving x22 alone.** discord's call site shows the caller
  filling that in: `stp x22, x8, [x19]` writes `callee->Parent` and
  `callee->ResumeParent` into the freshly allocated context. the sequence that
  looks like the return (`ldr x22, [x22]` then `ldr x0, [x22, #8]`, 100
  occurrences in discord against **0** of the reverse order) is a *resume
  funclet* recovering its own context from the finished callee's -- which is
  why the completing function must not touch x22.
- **a thrown error comes back in x20, not x21.** the resume funclet after this
  very call does `str x20, [ctx, #0x60]` then `cbz x20` to pick the success or
  failure path.

the first version of this completed immediately -- three instructions,
`mov x20, #0` / `ldr x0, [x22, #8]` / `br x0` -- which proved the abi but made
the prefetch timeout fire at once. **that is what stopped images loading**:
avatars, server icons, emoji and attachments were all being prefetched with a
timeout that expired before the fetch started, so every one of them was
cancelled. base ui elements, which are not prefetched, were fine. it is a good
illustration of the house rule here: a wrong answer does not crash.

so it now **forwards to ios 15's own `Task.sleep(nanoseconds:)` as an async
tail-branch** (`_$sScTss5NeverORszABRs_rlE5sleep11nanosecondsys6UInt64V_tYaKFZ`
in `libswift_Concurrency.dylib`, which the shim now links). that function is
`async throws -> Void`, the same continuation signature as the one being
replaced, so x22 is left alone and the context discord allocated becomes the
callee's with `Parent`/`ResumeParent` already correct -- there is no async
return sequence in the stub at all. it also means cancellation works, since
the real sleep is the one that checks it.

**`&deadline` is in x0**, measured with `probe_sleep.py` rather than assumed:
at the trap, x0 pointed at `[0x291177d694a6e000, 0x5899]`, an attosecond count
reading 418393.6 s -- the machine's uptime. x1 is `&tolerance` (a nil
`Optional<Duration>`), x2 `&clock`, and x3 the clock's metadata, which was
recognisable because it is *our* `ContinuousClock` record (kind word `0x200`).

the arithmetic is exact, not rounded:

    remaining = deadline - ContinuousClock.now        128-bit subs/sbcs,
                                                      csel to 0 if negative
    nanoseconds = remaining / 1e9

and `1e9 == 2**9 * 1953125`, so rather than a 128-bit division the remainder is
funnel-shifted down with one `extr #9` and divided once by the odd half.
`floor(floor(v / 512) / 1953125) == floor(v / 1e9)` for every v, so this is the
same answer a real division would give, and the shifted value always fits in
64 bits (60 s is 6e19 attoseconds, under 2^73).

the caller also reads the **context size** out of the async function pointer
(`ldr w0, [x8, #4]` on the `Tu` record, then `swift_task_alloc`), so async
function pointers target the real implementation instead of the shared trap,
and still declare 1024 bytes.

`shimverify.py` asserts the contract that would otherwise fail silently: the
deadline is read from `[x0]`, x22 is never written, `_clock_gettime` and then
ios 15's sleep are the only two imports used, the forward is the last
instruction and a `br` rather than a `blr`, the frame is dropped before it,
and the division is the `extr #9` + `udiv 1953125` pair.

### where it stands now

0 strong-missing on all four binaries; `checkshim.py`, `shimverify.py` and
`clockverify.py` all ok. the shim is 128 exports: 34 trap stubs left, 6 real
stubs and 9 clock symbols.

on the phone it now gets through launch to the point where **the ui is up and
dms load**, then stops on the storekit 2 price formatter:

    libdiscordshim.dylib  Product.priceFormatStyle.getter + 0
    Discord               +0x005ce94

the enclosing function at `image+0x5ca90` is discord's product serializer for
javascript: it reads `backing`, `description`, `displayName`, `displayPrice`,
`id`, `isFamilyShareable`, `jsonRepresentation`, `price`, `subscription` --
**all of which are real ios 15 storekit 2 symbols** -- and only
`priceFormatStyle` is ios 16.

this one is implementable rather than stubbable, and the pieces are all on
ios 15: `Decimal.FormatStyle.Currency` and its `init(code:locale:)` are in the
15 overlay (the metadata accessor the function already imports binds straight
to it, not to the shim), and so is `Locale.autoupdatingCurrent`. the catch is
the currency *code*: on ios 16 it comes from the product's storefront, and the
ios-16 way to ask (`Locale.currency`, `Locale.Currency.identifier`) is itself
in the trap list. so it would have to come from ios 15's
`Locale.currencyCode`, i.e. the device locale rather than the storefront --
right for almost everyone, wrong for anyone whose app store region differs
from their locale. that is a user-visible choice rather than a technical one.

## it runs

`Product.priceFormatStyle` was the last thing in the way. with it implemented,
**discord 350.0 launches and stays up on the test device, 15.7.7 / 19H357** --
through the app delegate, the main run loop, the javascript runtime, react
native's turbomodule setup, into the ui, and on into normal use. no crash
report has been produced since.

### Product.priceFormatStyle

ios 16 only, but the only ios-16 thing in its caller. the enclosing function at
`image+0x5ca90` is discord's storekit product serializer for javascript, and
the nine other properties it reads -- `backing`, `description`, `displayName`,
`displayPrice`, `id`, `isFamilyShareable`, `jsonRepresentation`, `price`,
`subscription` -- are all real ios 15 storekit 2 symbols.

what the call site does with the result decided the approach:

    ldr x21, [x19, #0x10]
    mov x8, x21                    ; the result buffer
    mov x20, x22                   ; self = the Product
    bl  "Product.priceFormatStyle"
    mov x20, x21
    bl  "Decimal.FormatStyle.Currency.currencyCode.getter"
    str x0, [x25, #0x210]          ; ...into the "currency" dict key
    str x1, [x25, #0x218]

so all discord wants out of it is the currency code string. and everything
needed to produce a genuine one is in the ios 15 overlay:
`Decimal.FormatStyle.Currency.init(code:locale:)`,
`Locale.autoupdatingCurrent` and `Locale.currencyCode`. the type's metadata
accessor is already bound to ios 15 rather than to the shim, so the result
buffer the caller hands over in x8 is sized by ios 15's own metadata and the
value fits exactly.

the implementation is therefore real, not a stand-in:

    Decimal.FormatStyle.Currency(code: Locale.autoupdatingCurrent.currencyCode
                                       ?? "USD",
                                 locale: .autoupdatingCurrent)

abi read off ios 15's own initialiser: x8 = result, x0/x1 = the code string,
x2 = `&locale` (9 bytes -- `ldr x8, [x2]` then `ldrb w9, [x2, #8]`, matching
`Locale`'s size of 9 and stride of 16). the nil case is handled branchlessly:
a nil `String?` has a zero object word, so `cmp x1, #0` plus two `csel`s swap
in `"USD"`. the fallback literal is built as a swift small string -- utf8
bytes in the first word, `0xE0 | count` in the second word's top byte --
which is exactly how discord builds the `"currency"` key a few instructions
earlier.

**the one compromise:** `self`, the `Product`, is ignored. on ios 16 the
currency comes from the product's app store storefront, and every ios-16 way
to ask for it (`Locale.currency`, `Locale.Currency.identifier`) is itself still
a trap stub here. so this reports the *device locale's* currency, which is
right unless someone's app store region differs from their locale. the price
string discord actually displays comes from `Product.displayPrice`, which is a
real ios 15 symbol, so only this one metadata field is affected.

### final numbers

| binary | ok | special | weak-missing | **strong missing** |
|---|---|---|---|---|
| Discord | 9968 | 941 | 364 | **0** |
| NotificationService | 1077 | - | 16 | **0** |
| Share | 1465 | - | 26 | **0** |
| BroadcastUpload | 2146 | 8 | 6 | **0** |

`checkshim.py` ok (header slack 32 bytes), `clockverify.py` ok,
`shimverify.py`: 36 retain/release + 4 URL getters + 1 ret-self + 1
sleep-forward + 1 price-format, 0 mismatched.

the shim is 128 exports, of which **33 are still `brk` traps**. that is not a
list of things left to do -- it is the set of ios-16 symbols discord links but
has not executed on this device. the trap-mode shim means any one of them that
does get reached names itself in the crash report, so the loop stays the same:
`./crash_last.sh` -> `callsite.py` -> decide stub, neuter, or implement.

| | implemented for real | |
|---|---|---|
| 4 | `URL.cachesDirectory` and the other directory getters | objc + the ios 15 bridge |
| 1 | `CGPathRef.subtracting(_:using:)` | answers with the receiver |
| 1 | `Task.sleep(until:tolerance:clock:)` | real, forwards to ios 15's `sleep(nanoseconds:)` |
| 1 | `Product.priceFormatStyle` | real, device-locale currency |
| 9 | the `ContinuousClock` / `Duration` family | real metadata + arithmetic |
| 36 | `objc_retain_xN` / `objc_release_xN` | real register-ABI stubs |

plus one neutered call site in discord's own code (the window-scene geometry
request) and the four ios-16 frameworks weakened so dyld will load the image.

## bundled icons do not render: snowboard, not us

symptom: remote images (avatars, emoji, stickers, attachments) load; every
**bundled** icon is blank -- settings rows, chevrons, the channel-list glyphs.
no crash. the javascript log is clean.

it is not the patch. the chain, every link measured on the device:

1. the icon pngs are in the bundle byte-identical to the ipa, and discord's
   ota resolver (`resolvedOTAAssetURL:`) hands javascript the correct
   `file:///.../Discord.app/assets/...Icon@2x.png` for each one.
2. those go to react native's `RCTImageManager requestImage:surfaceId:`
   (fabric `<Image>`), not to discord's own `DCDFastImageComponentView`,
   which only ever sees `https://cdn.discordapp.com/...` -- that is why remote
   images are unaffected.
3. `RCTImageManager` was created with **`initWithImageLoader:nil`**: every
   local image request is a message to nil, which does nothing, fails
   nothing and opens no file (an `fs_usage` capture saw 0 successful opens of
   any icon).
4. it is nil because `[RCTTurboModuleManager moduleForName:"RCTImageLoader"]`
   returns nil: `_getModuleClassFromName:` resolves `"ImageLoader"` with
   `NSClassFromString(@"ImageLoader")` *before* trying `"RCTImageLoader"`,
   and `_shouldCreateObjCModule:` then rejects the class it found.
5. the class it found is **`ImageLoader` from `SnowboardBase.dylib`** -- the
   snowboard theming tweak, injected into every UIKit app by the jailbreak. it
   is a plain `NSObject` subclass, not a react native module.

`probe_modclass.py` reproduces step 5 in one run. this is the "icons vanish
in some discord updates" report seen on jailbroken phones: it appears in any
build whose react native resolves image modules by bare name.

the fix is outside this pipeline: keep snowboard (or its base dylib) from
injecting into discord. a binary patch would have to reorder the lookup in
`_getModuleClassFromName:` (Discord image+0x7ece34) to try `RCT%s` first, and
discord's own modules rely on the bare name, so it is not a one-instruction
change.

two other things found on the way:

- `Task.sleep(until:)` is now real (see the async abi section). it was a
  genuine bug, just not this one.
- **"clear cache" in discord's developer settings aborts**:
  `[SFSafariViewControllerDataStore defaultDataStore]` (ios 16) lands on the
  shim's inert class, which has no methods. Discord image+0xc7f8ec.

probes added: `probe_relstat.py` (stat64 with a frame-pointer backtrace),
`probe_bundleres.py` (`URLForResource:withExtension:subdirectory:` arguments
and result), `probe_hits.py` (generic: `OFFS=` image offsets, prints caller,
x2 and the return), `probe_modclass.py` (which class and which image a module
name resolves to), `probe_decode.py` (ImageIO entry points).
