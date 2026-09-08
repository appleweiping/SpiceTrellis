package circuitir

import (
	"bytes"
	"crypto/sha256"
	"encoding/hex"
	"encoding/json"
	"fmt"
	"sort"
	"strconv"
	"unicode/utf8"
)

// MarshalCanonical emits the compact, key-sorted UTF-8 form used by the
// language-neutral fingerprint contract. The returned bytes contain no final
// newline.
func (document Document) MarshalCanonical() ([]byte, error) {
	if err := document.Validate(); err != nil {
		return nil, err
	}
	encoded, err := json.Marshal(document)
	if err != nil {
		return nil, fmt.Errorf("circuit IR: encode document: %w", err)
	}
	generic, err := decodeGeneric(encoded)
	if err != nil {
		return nil, fmt.Errorf("circuit IR: inspect encoded document: %w", err)
	}
	if err := validateShape(generic); err != nil {
		return nil, fmt.Errorf("circuit IR: non-canonical in-memory shape: %w", err)
	}
	var buffer bytes.Buffer
	if err := writeCanonical(&buffer, generic); err != nil {
		return nil, err
	}
	if buffer.Len() > MaxBytes {
		return nil, fmt.Errorf("circuit IR: encoded document exceeds the %d-byte limit", MaxBytes)
	}
	return buffer.Bytes(), nil
}

// Fingerprint returns the lowercase SHA-256 of MarshalCanonical.
func (document Document) Fingerprint() (string, error) {
	canonical, err := document.MarshalCanonical()
	if err != nil {
		return "", err
	}
	digest := sha256.Sum256(canonical)
	return hex.EncodeToString(digest[:]), nil
}

func writeCanonical(buffer *bytes.Buffer, value any) error {
	switch typed := value.(type) {
	case map[string]any:
		keys := make([]string, 0, len(typed))
		for key := range typed {
			keys = append(keys, key)
		}
		sort.Strings(keys)
		buffer.WriteByte('{')
		for index, key := range keys {
			if index != 0 {
				buffer.WriteByte(',')
			}
			writeJSONString(buffer, key)
			buffer.WriteByte(':')
			if err := writeCanonical(buffer, typed[key]); err != nil {
				return err
			}
		}
		buffer.WriteByte('}')
	case []any:
		buffer.WriteByte('[')
		for index, item := range typed {
			if index != 0 {
				buffer.WriteByte(',')
			}
			if err := writeCanonical(buffer, item); err != nil {
				return err
			}
		}
		buffer.WriteByte(']')
	case string:
		writeJSONString(buffer, typed)
	case json.Number:
		buffer.WriteString(typed.String())
	case bool:
		buffer.WriteString(strconv.FormatBool(typed))
	case nil:
		buffer.WriteString("null")
	default:
		return fmt.Errorf("circuit IR: cannot canonicalize value of type %T", value)
	}
	return nil
}

func writeJSONString(buffer *bytes.Buffer, value string) {
	buffer.WriteByte('"')
	for _, character := range value {
		switch character {
		case '"', '\\':
			buffer.WriteByte('\\')
			buffer.WriteRune(character)
		case '\b':
			buffer.WriteString(`\b`)
		case '\f':
			buffer.WriteString(`\f`)
		case '\n':
			buffer.WriteString(`\n`)
		case '\r':
			buffer.WriteString(`\r`)
		case '\t':
			buffer.WriteString(`\t`)
		default:
			if character < 0x20 {
				fmt.Fprintf(buffer, `\u%04x`, character)
			} else if character == utf8.RuneError {
				// Validate rejects U+FFFD so an escaped lone surrogate cannot be
				// normalized into a different accepted document by encoding/json.
				buffer.WriteRune(character)
			} else {
				buffer.WriteRune(character)
			}
		}
	}
	buffer.WriteByte('"')
}
