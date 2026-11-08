#!/usr/bin/env bash
# Bambuddy: let Bambu Studio and OrcaSlicer connect to this Bambuddy's virtual
# printers.
#
# The slicers only talk to printers whose certificate is listed in their file
# printer.cer. This script adds this Bambuddy's certificate to every
# printer.cer it finds (Linux and macOS), keeps a copy of the original next to
# it (printer.cer.before-bambuddy), and changes nothing where the certificate
# is already there. Slicer updates and reinstalls replace printer.cer, so run
# it again after one.
#
#   bash bambuddy-slicer-cert.sh
#
# It asks for your password (sudo) when a slicer is installed system-wide.
#
# Made by Bambuddy for the certificate with SHA-256 fingerprint
# __BAMBUDDY_CA_FINGERPRINT__

set -u

CA_PEM=$(
	cat <<'BAMBUDDY_CA_PEM'
__BAMBUDDY_CA_PEM__
BAMBUDDY_CA_PEM
)

# Only for Bambuddy's own tests: look for printer.cer under these directories
# instead of the usual places, and never use sudo.
TEST_ROOTS=${BAMBUDDY_CERT_TEST_ROOTS:-}

# Started with "sudo bash ...": look in the home folder of whoever ran sudo,
# not root's, for Flatpaks and extracted AppImages.
if [ -z "$TEST_ROOTS" ] && [ "$(id -u)" -eq 0 ] && [ -n "${SUDO_USER:-}" ]; then
	case "$SUDO_USER" in
	*[!A-Za-z0-9._-]*) ;;
	*) HOME=$(eval echo "~$SUDO_USER") ;;
	esac
fi

# The base64 text of each certificate in a PEM file, one per line, without
# line breaks: the same certificate gives the same line, however the file
# wraps or ends its lines.
cert_bodies() {
	tr -d '\r' <"$1" | awk '
		/-----BEGIN CERTIFICATE-----/ { inside = 1; body = ""; next }
		/-----END CERTIFICATE-----/ { if (inside) print body; inside = 0; next }
		inside { gsub(/[ \t]/, ""); body = body $0 }
	'
}

# The PEM text of certificate number $2 (from 1) in file $1.
cert_block() {
	tr -d '\r' <"$1" | awk -v want="$2" '
		/-----BEGIN CERTIFICATE-----/ { n++ }
		n == want && !done { print }
		n == want && /-----END CERTIFICATE-----/ { done = 1 }
	'
}

cert_subject() {
	openssl x509 -noout -subject -nameopt RFC2253 2>/dev/null
}

find_cert_files() {
	if [ -n "$TEST_ROOTS" ]; then
		for root in $TEST_ROOTS; do
			find "$root" -type f -name printer.cer 2>/dev/null
		done
		return
	fi
	if [ "$(uname -s)" = "Darwin" ]; then
		for apps in /Applications "$HOME/Applications"; do
			[ -d "$apps" ] || continue
			find "$apps" -maxdepth 1 -iname '*.app' \( -iname '*bambu*' -o -iname '*orca*' \) 2>/dev/null |
				while IFS= read -r app; do
					[ -f "$app/Contents/Resources/cert/printer.cer" ] && printf '%s\n' "$app/Contents/Resources/cert/printer.cer"
				done
		done
		return
	fi
	# Packages (/usr, /opt), Flatpak (system and user), and AppImages
	# extracted into the home folder (squashfs-root).
	for root in /usr/share /usr/local/share /usr/lib /opt /var/lib/flatpak/app "$HOME/.local/share/flatpak/app"; do
		[ -d "$root" ] || continue
		# A Flatpak's file sits 10 levels down
		# (<id>/x86_64/stable/<commit>/files/share/<slicer>/resources/cert).
		find "$root" -maxdepth 12 -type f -name printer.cer \( -ipath '*bambu*' -o -ipath '*orca*' \) 2>/dev/null
	done
	# An extracted AppImage is a folder squashfs-root, wherever it was
	# extracted; it is a slicer's when it holds the slicer's program.
	find "$HOME" -maxdepth 5 -type d -name squashfs-root 2>/dev/null |
		while IFS= read -r extracted; do
			if [ -n "$(find "$extracted" -maxdepth 2 \( -iname '*bambu*' -o -iname '*orca*' \) 2>/dev/null | head -n 1)" ]; then
				find "$extracted" -maxdepth 6 -type f -name printer.cer 2>/dev/null
			fi
		done
}

file_mode() {
	stat -c '%a' "$1" 2>/dev/null || stat -f '%Lp' "$1" 2>/dev/null || echo 644
}

# Adds the certificate to one printer.cer. Prints one line saying what it did.
# Returns 0 when the file has the certificate afterwards, 1 when it failed.
update_file() {
	local file=$1 dir sudo="" ours count i block subject drop="" tmp mode
	dir=$(dirname "$file")
	ours=$(printf '%s\n' "$CA_PEM" | awk '/BEGIN/{i=1;next} /END/{i=0} i{gsub(/[ \t\r]/,"");b=b $0} END{print b}')

	if cert_bodies "$file" | grep -qxF "$ours"; then
		echo "  already there: $file"
		return 0
	fi

	# A slicer uses the first certificate with the right name, so an earlier
	# issue of this CA (same name, same key, other dates) would hide this
	# one: it goes. Not the bare legacy name: older Bambuddy installs all
	# share it, and it may be another one's.
	if command -v openssl >/dev/null 2>&1; then
		subject=$(printf '%s\n' "$CA_PEM" | cert_subject)
		[ "$subject" = "subject=CN=Virtual Printer CA" ] && subject=""
		count=$(cert_bodies "$file" | wc -l | tr -d ' ')
		i=1
		while [ "$i" -le "$count" ]; do
			block=$(cert_block "$file" "$i")
			if [ -n "$subject" ] && [ "$(printf '%s\n' "$block" | cert_subject)" = "$subject" ]; then
				drop="$drop $i"
			fi
			i=$((i + 1))
		done
	fi

	if [ -z "$TEST_ROOTS" ] && [ ! -w "$dir" ]; then
		sudo="sudo"
	fi

	tmp=$(mktemp "${TMPDIR:-/tmp}/bambuddy-cert.XXXXXX") || return 1
	tr -d '\r' <"$file" | awk -v drop="$drop " '
		/-----BEGIN CERTIFICATE-----/ { n++; skip = index(drop, " " n " ") > 0 }
		!skip { print }
		skip && /-----END CERTIFICATE-----/ { skip = 0 }
	' >"$tmp"
	printf '%s\n' "$CA_PEM" >>"$tmp"
	mode=$(file_mode "$file")

	# Written next to the file and then moved over it, so the slicer never
	# sees half a file, and a Flatpak's shared copy is not changed in place.
	if { [ -e "$file.before-bambuddy" ] || $sudo cp -p "$file" "$file.before-bambuddy"; } &&
		$sudo cp "$tmp" "$file.bambuddy-new" &&
		$sudo chmod "$mode" "$file.bambuddy-new" &&
		$sudo mv -f "$file.bambuddy-new" "$file"; then
		rm -f "$tmp"
		if [ -n "$drop" ]; then
			echo "  replaced an older Bambuddy certificate: $file"
		else
			echo "  added: $file"
		fi
		return 0
	fi
	rm -f "$tmp"
	$sudo rm -f "$file.bambuddy-new" 2>/dev/null
	echo "  FAILED: $file"
	if [ "$(uname -s)" = "Darwin" ]; then
		echo "    macOS may block changes to apps: allow your terminal under"
		echo "    System Settings > Privacy & Security > App Management, then run this again."
	fi
	return 1
}

main() {
	local files failed=0 found=0 file
	echo "Bambuddy slicer certificate"
	echo

	files=$(find_cert_files | sort -u)
	if [ -n "$files" ]; then
		while IFS= read -r file; do
			found=1
			update_file "$file" || failed=1
		done <<EOF
$files
EOF
	fi

	if [ -z "$TEST_ROOTS" ] && [ "$(uname -s)" != "Darwin" ]; then
		appimages=$(find "$HOME" -maxdepth 3 -type f \( -iname '*bambu*.appimage' -o -iname '*orca*.appimage' \) 2>/dev/null)
		if [ -n "$appimages" ]; then
			echo
			echo "An AppImage can't be changed. To use one with Bambuddy, extract it once"
			echo "and start the slicer from the extracted copy:"
			echo "  ./<slicer>.AppImage --appimage-extract    (creates squashfs-root)"
			echo "  bash $0                                    (run this script again)"
			echo "  ./squashfs-root/AppRun                     (start the slicer like this from now on)"
			echo "Found: $(printf '%s' "$appimages" | tr '\n' ' ')"
		fi
	fi

	echo
	if [ "$found" -eq 0 ]; then
		echo "No Bambu Studio or OrcaSlicer installation found."
		echo "See the Bambuddy wiki (Virtual Printer > Certificate Installation) to add it by hand."
		return 2
	fi
	if [ "$failed" -ne 0 ]; then
		echo "Not every slicer could be updated, see above."
		return 1
	fi
	if [ -z "$TEST_ROOTS" ] && pgrep -if 'bambu.?studio|orca.?slicer' >/dev/null 2>&1; then
		echo "Done. A slicer is running: quit it completely and start it again."
	else
		echo "Done. The slicer uses the certificate the next time it starts."
	fi
	return 0
}

main "$@"
