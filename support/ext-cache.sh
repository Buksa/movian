#!/bin/sh

set -eu

mode=${1:-}
case "$mode" in
  check)
    [ "$#" -eq 8 ] || {
      echo "usage: $0 check DEP FLAVOR CONFIG_HASH ARTIFACTS STAMP C BUILDDIR" >&2
      exit 2
    }
    ;;
  build)
    [ "$#" -eq 9 ] || {
      echo "usage: $0 build DEP FLAVOR CONFIG_HASH ARTIFACTS STAMP C BUILDDIR CACHE_ENABLED" >&2
      exit 2
    }
    ;;
  *)
    echo "usage: $0 check|build ..." >&2
    exit 2
    ;;
esac

dep=$2
flavor=$3
config_hash=$4
artifacts=$5
stamp=$6
C=$7
BUILDDIR=$8
cache_enabled=${9:-yes}
source_dir="$C/ext/$dep"
install_dir="$BUILDDIR/inst"

gitlink=$(git ls-tree HEAD -- "ext/$dep" | awk 'NR == 1 { print $3 }')
if [ -z "$gitlink" ]; then
  echo "ext-cache: $dep has no gitlink in HEAD" >&2
  exit 1
fi

actual=$(git -C "$source_dir" rev-parse HEAD)
dirty=no
if [ "$actual" != "$gitlink" ] ||
    [ -n "$(git -C "$source_dir" status --porcelain --untracked-files=normal)" ]; then
  dirty=yes
fi

key="$gitlink $flavor $config_hash"
artifacts_present=yes
for artifact in $artifacts; do
  if [ ! -f "$artifact" ]; then
    artifacts_present=no
    break
  fi
done

if [ "$dirty" = no ] && [ "$artifacts_present" = yes ] &&
    [ -f "$stamp" ] && [ "$(cat "$stamp")" = "$key" ]; then
  exit 0
fi
if [ "$mode" = check ]; then
  rm -f "$stamp"
  exit 0
fi

build_dep() {
  make -f "$C/ext/$dep.mk" build
  for artifact in $artifacts; do
    if [ ! -f "$artifact" ]; then
      echo "ext-cache: $dep build did not install $artifact" >&2
      exit 1
    fi
  done
}

if [ "$dirty" = yes ]; then
  echo "ext-cache: $dep is dirty; rebuilding without cache"
  build_dep
  mkdir -p "$(dirname "$stamp")"
  printf 'dirty %s\n' "$key" >"$stamp"
  exit 0
fi

case "$dep" in
  libav)
    cache_paths="
      include/libavcodec include/libavdevice include/libavformat
      include/libavresample include/libavutil include/libswresample
      include/libswscale lib/libavcodec.a lib/libavdevice.a
      lib/libavformat.a lib/libavresample.a lib/libavutil.a
      lib/libswresample.a lib/libswscale.a lib/pkgconfig/libavcodec.pc
      lib/pkgconfig/libavdevice.pc lib/pkgconfig/libavformat.pc
      lib/pkgconfig/libavresample.pc lib/pkgconfig/libavutil.pc
      lib/pkgconfig/libswresample.pc lib/pkgconfig/libswscale.pc"
    ;;
  libsmb2)
    cache_paths="include/smb2 lib/libsmb2.a lib/pkgconfig/libsmb2.pc lib/cmake/libsmb2"
    ;;
  libyuv)
    cache_paths="include/libyuv include/libyuv.h lib/libyuv.a"
    ;;
  *)
    echo "ext-cache: unsupported dependency $dep" >&2
    exit 1
    ;;
esac

if [ "$cache_enabled" != yes ]; then
  echo "ext-cache: $dep cache disabled; building"
  build_dep
  mkdir -p "$(dirname "$stamp")"
  printf '%s\n' "$key" >"$stamp"
  exit 0
fi

cache_base=${MOVIAN_EXT_CACHE_DIR:-${XDG_CACHE_HOME:-${HOME:?}/.cache}/movian-ext}
cache_key=$(printf '%s\n' "$key" | sha256sum | awk '{print $1}')
dep_cache="$cache_base/$dep"
entry="$dep_cache/$cache_key"

cache_valid() {
  [ -f "$entry/key" ] &&
    [ "$(cat "$entry/key")" = "$key" ] &&
    [ -f "$entry/payload.tar" ] &&
    [ -f "$entry/payload.sha256" ] &&
    (cd "$entry" && sha256sum -c payload.sha256 >/dev/null 2>&1) &&
    tar -tf "$entry/payload.tar" >/dev/null 2>&1
}

install_complete() {
  for path in $cache_paths; do
    [ -e "$install_dir/$path" ] || return 1
  done
}

if cache_valid; then
  echo "ext-cache: $dep cache hit"
  mkdir -p "$install_dir"
  for path in $cache_paths; do
    rm -rf "$install_dir/$path"
  done
  tar -xf "$entry/payload.tar" -C "$install_dir"
  if ! install_complete; then
    echo "ext-cache: $dep cache entry is incomplete; rebuilding" >&2
    rm -rf "$entry"
    build_dep
  else
    for artifact in $artifacts; do
      touch "$artifact"
    done
  fi
else
  if [ -e "$entry" ]; then
    echo "ext-cache: $dep cache entry is invalid; rebuilding"
    rm -rf "$entry"
  else
    echo "ext-cache: $dep cache miss; building"
  fi
  build_dep
fi

mkdir -p "$(dirname "$stamp")"
printf '%s\n' "$key" >"$stamp"

if ! cache_valid; then
  mkdir -p "$dep_cache"
  tmp_entry=$(mktemp -d "$dep_cache/.tmp.XXXXXX")
  trap 'rm -rf "$tmp_entry"' EXIT HUP INT TERM
  mkdir -p "$tmp_entry/inst"
  for path in $cache_paths; do
    if [ ! -e "$install_dir/$path" ]; then
      echo "ext-cache: $dep install is incomplete: missing $path" >&2
      exit 1
    fi
    mkdir -p "$tmp_entry/inst/$(dirname "$path")"
    cp -a "$install_dir/$path" "$tmp_entry/inst/$(dirname "$path")/"
  done
  tar -cf "$tmp_entry/payload.tar" -C "$tmp_entry/inst" $cache_paths
  printf '%s\n' "$key" >"$tmp_entry/key"
  (cd "$tmp_entry" && sha256sum payload.tar >payload.sha256)
  rm -rf "$tmp_entry/inst" "$entry"
  mv "$tmp_entry" "$entry"
  trap - EXIT HUP INT TERM
  echo "ext-cache: $dep cache populated"
fi
