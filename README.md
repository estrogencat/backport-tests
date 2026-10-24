# backport-tests

a patch pipeline that makes current discord ipas run on **ios 15**.

discord raised `MinimumOSVersion` to 16.0 at 349.0. this takes a decrypted
ipa, rewrites it to load on ios 15, and checks every import against a real
ios 15 dyld shared cache before you ever install it. no apple toolchain is
needed: everything, including the support dylib, is emitted from python.

tested with discord **350.0** (115636) on ios **15.7.7**: it launches and
stays up.

no discord binaries are included here. bring your own decrypted ipa.

## what it does

- **repoints imports.** the ios 16 sdk moved the swift foundation overlay out
  of `libswiftFoundation.dylib` into `Foundation.framework`, along with a few
  other moves. about 580 imports are retargeted to where ios 15 actually
  exports them (`patch.py`).
- **builds a shim dylib** (`mkshim.py`) for the ios 16 symbols that have no
  ios 15 home. the mach-o is written byte by byte. what it contains:
  - real implementations where it matters: the `objc_retain_xN` /
    `objc_release_xN` register-abi stubs, the `URL.cachesDirectory` family,
    `ContinuousClock` / `Duration` with hand-built swift metadata,
    `Task.sleep(until:tolerance:clock:)` forwarded to ios 15's
    `Task.sleep(nanoseconds:)`, and `Product.priceFormatStyle`
  - `brk` traps for everything else, so a symbol that is ever actually
    reached names itself in the crash report instead of misbehaving quietly
- **lowers minos** to 15.0 in every `LC_BUILD_VERSION` and `Info.plist`,
  drops `LockscreenWidget.appex` (ios 16.2+), and neuters one discord call
  site that drives an ios 16 window-scene api (`neuter.py`).
- **verifies.** `verify.py` resolves every import of all four binaries
  against the ios 15 cache, the shim and the bundled frameworks, and the
  build fails unless **strong-missing is 0**. `checkshim.py`, `shimverify.py`
  and `clockverify.py` re-derive the shim's structure and each stub's register
  contract from the emitted bytes, because a wrong abi in a stub does not
  crash, it silently returns the wrong value.

signing is left to trollstore.

## requirements

- python 3.12+
- `capstone` (`pip install capstone`) for the verification and debug scripts
- an extracted **ios 15 dyld shared cache** (`dyld_shared_cache_arm64*`,
  matching your device's ios version, e.g. pulled from an ipsw)
- a **decrypted** discord ipa
- a jailbroken or trollstore device for installing

## usage

    export DSC=/path/to/extracted/dsc
    export IPA=/path/to/discord-350.0-decrypted.ipa
    ./run.sh

the patched ipa lands in `out/<VER>/Discord<VER>-ios15.ipa`. install it with
trollstore however you like.

the next release is one line, and prints how the shim surface changed:

    VER=351 IPA=/path/to/new.ipa PREV=350 ./run.sh

`out/` is entirely regenerable. `syms/<VER>/` holds the per-binary shim symbol
lists, which are what to diff between releases.

### github actions

the **build** workflow (actions -> build -> run workflow) does the same in a
runner and uploads the patched ipa as an artifact. it needs:

- `ipa_url`: a direct link to a decrypted ipa
- one of `dsc_url` (a zip or tar of an extracted shared cache) or `ipsw_url`
  (an ios 15 ipsw; only the cache is pulled out of it, with
  [ipsw](https://github.com/blacktop/ipsw))

any of these can be set as a repository secret (`IPA_URL`, `DSC_URL`,
`IPSW_URL`) instead of an input, which keeps the links off the run page. the
extracted cache is kept in the actions cache between runs.

artifacts on a public repository can be downloaded by anyone signed in to
github, and they are kept for 7 days.

### on-device helpers

if the phone is reachable over ssh as root, these drive it directly:

    export PHONE=<phone address>        # SSH_PORT, KEY_ROOT also read
    ./install.sh       # upload and install via trollstorehelper
    ./launch.sh        # launch, report whether it stayed up
    ./crash_last.sh    # pull the newest crash report and symbolize it

**launch twice after installing.** the first launch after an install can run
against a stale mapping of the previous bundle.

### debugging

`./dbg.sh probe_<name>.py` starts debugserver on the phone, launches discord
and runs a probe against it over gdb-remote. the probes are small, single
purpose tools: `probe_hits.py` (break on image offsets, print caller and
return), `probe_relstat.py` (who stats a path), `probe_modclass.py` (which
class a react native module name resolves to), and others.

the probes use image offsets for discord 350.0 and the 15.7.7 cache. some
hardcode shared-cache addresses that only stay valid until the phone reboots.
treat them as starting points for a new release, not as fixed tools.

`python -E -P callsite.py <binary> <imageOffset>` turns a crash offset into
the surrounding code and the objc selector being sent.

## known issues

- **bundled icons are blank with snowboard installed.** this is the tweak,
  not the patch: `SnowboardBase.dylib` defines a global objc class named
  `ImageLoader`, react native finds it before its own `RCTImageLoader`, and
  every local image request goes to a nil loader. exclude discord from
  snowboard injection.
- **"clear cache"** in discord's developer settings crashes. it calls
  `SFSafariViewControllerDataStore` (ios 16).
- `Product.priceFormatStyle` reports the device locale's currency rather than
  the app store storefront's.

## notes

[`docs/notes.md`](docs/notes.md) is the full working record, in order: how
each gap was found, measured on the device and closed.
