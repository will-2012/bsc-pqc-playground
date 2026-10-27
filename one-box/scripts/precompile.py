"""Build the isolated v2 client from the pinned BSC snapshot and local extension."""
import io
import shutil
import subprocess
import tarfile

from devnet import ROOT, BOX, BUILD, command, digest, read, write, binary_info

WORK = BUILD / "v2"
SOURCE = WORK / "bsc"
BINARY = WORK / "geth"


def prepare(work=WORK):
    source = work / "bsc"
    upstream = ROOT / "external/bsc"
    if command(["git", "status", "--porcelain"], cwd=upstream).strip():
        raise RuntimeError("Build v2 from a clean, pinned BSC submodule")
    commit = command(["git", "rev-parse", "HEAD"], cwd=upstream).strip()
    work.mkdir(parents=True, exist_ok=True)
    if source.exists():
        shutil.rmtree(source)
    source.mkdir()
    archive = subprocess.check_output(["git", "archive", "HEAD"], cwd=upstream)
    with tarfile.open(fileobj=io.BytesIO(archive)) as tar:
        tar.extractall(source, filter="data")
    log = work / "build.log"
    patch = BOX / "patches/bsc-slh-v2.patch"
    command(["patch", "--batch", "-p1", "-i", patch], log, source)
    for path in (BOX / "precompile/v2").glob("*.go"):
        shutil.copy2(path, source / "core/vm" / path.name)
    shutil.copy2(BOX / "precompile/v2/testdata/slh_v2.json", source / "core/vm/testdata/slh_v2.json")
    dep = read(BOX / "precompile/v2/dependency.json")
    command(["go", "mod", "edit", f'-require={dep["module"]}@{dep["version"]}'], log, source)
    sums = source / "go.sum"
    with sums.open("a") as out:
        out.write(f'\n{dep["module"]} {dep["version"]} {dep["sum"]}\n')
        out.write(f'{dep["module"]} {dep["version"]}/go.mod {dep["goModSum"]}\n')
    return commit


def build():
    print("Building v2 SLH precompile client; v1 remains unchanged...", flush=True)
    commit = prepare()
    log = WORK / "build.log"
    command(["go", "test", "-mod=readonly", "-tags", "pqprecompile", "./core/vm", "-run", "^TestSLHV2", "-count=1"],
            log, SOURCE, timeout=600)
    command(["go", "test", "-mod=readonly", "github.com/trailofbits/go-slh-dsa/slh_dsa", "-run", "^TestKatSigVer$", "-count=1"],
            log, SOURCE, timeout=300)
    command(["go", "build", "-mod=readonly", "-tags", "pqprecompile", "-buildvcs=false", "-trimpath", "-o", BINARY, "./cmd/geth"],
            log, SOURCE, timeout=1800)
    info = binary_info(BINARY)
    info.update(source_commit=commit, build_tag="pqprecompile",
                extension_files={str(p.relative_to(BOX)): digest(p) for p in (BOX / "precompile/v2").rglob("*") if p.is_file()},
                patch_sha256=digest(BOX / "patches/bsc-slh-v2.patch"), dependency=read(BOX / "precompile/v2/dependency.json"))
    write(WORK / "binary.json", info)
    print(f"Built {BINARY}", flush=True)
