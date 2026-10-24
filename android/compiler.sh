#!/usr/bin/env bash
# Compile le lanceur « Accueil » en APK signé, sans Gradle : aapt2 → javac → d8 → zipalign → apksigner.
# Résultat : android/Accueil.apk
#
# Prérequis : Android SDK (build-tools + une plateforme android-30 ou plus) et un JDK 17+.
# Clé de signature : $KEYSTORE (ou android/accueil.keystore), mot de passe $KEYSTORE_PASS.
# Si aucune clé n'existe, une clé locale est créée (gardez-la pour pouvoir mettre à jour l'appli).
set -euo pipefail
ICI="$(cd "$(dirname "$0")" && pwd)"

SDK="${ANDROID_HOME:-${ANDROID_SDK_ROOT:-}}"
for d in "$SDK" "$HOME/Library/Android/sdk" "$HOME/Android/Sdk" "/usr/local/lib/android/sdk"; do
  [[ -n "$d" && -d "$d/build-tools" ]] && { SDK="$d"; break; }
done
[[ -d "$SDK/build-tools" ]] || { echo "Android SDK introuvable : définissez ANDROID_HOME" >&2; exit 1; }
BT="$SDK/build-tools/$(ls "$SDK/build-tools" | sort -V | tail -1)"
PLATEFORME="$SDK/platforms/$(ls "$SDK/platforms" | grep -E '^android-[0-9]+$' | sort -V | tail -1)/android.jar"
if [[ -z "${JAVA_HOME:-}" && -d "/Applications/Android Studio.app/Contents/jbr/Contents/Home" ]]; then
  export JAVA_HOME="/Applications/Android Studio.app/Contents/jbr/Contents/Home"
fi
[[ -n "${JAVA_HOME:-}" ]] && export PATH="$JAVA_HOME/bin:$PATH"
VERSION="${VERSION:-1}"
echo "SDK : $BT, $(basename "$(dirname "$PLATEFORME")")"

B="$ICI/build"
rm -rf "$B" && mkdir -p "$B/gen" "$B/classes" "$B/dex"

"$BT/aapt2" compile --dir "$ICI/res" -o "$B/res.zip"
"$BT/aapt2" link -I "$PLATEFORME" --manifest "$ICI/AndroidManifest.xml" "$B/res.zip" \
  --java "$B/gen" --min-sdk-version 28 --target-sdk-version 30 \
  --version-code "$VERSION" --version-name "1.$VERSION" -o "$B/base.apk"

javac -nowarn --release 11 -classpath "$PLATEFORME" -d "$B/classes" \
  $(find "$ICI/src" "$B/gen" -name '*.java')
"$BT/d8" --release --min-api 28 --lib "$PLATEFORME" --output "$B/dex" $(find "$B/classes" -name '*.class')
(cd "$B/dex" && zip -q -j "$B/base.apk" classes.dex)

CLE="${KEYSTORE:-$ICI/accueil.keystore}"
MDP="${KEYSTORE_PASS:-accueil}"
if [[ ! -f "$CLE" ]]; then
  echo "Aucune clé : création de $CLE (à conserver pour les mises à jour)"
  keytool -genkeypair -keystore "$CLE" -alias accueil -keyalg RSA -keysize 2048 \
    -validity 10000 -storepass "$MDP" -keypass "$MDP" -dname "CN=Accueil TV" >/dev/null 2>&1
fi
"$BT/zipalign" -f -p 4 "$B/base.apk" "$B/aligned.apk"
"$BT/apksigner" sign --ks "$CLE" --ks-pass "pass:$MDP" --key-pass "pass:$MDP" \
  --out "$ICI/Accueil.apk" "$B/aligned.apk"
echo "APK : $ICI/Accueil.apk ($(du -h "$ICI/Accueil.apk" | cut -f1))"
