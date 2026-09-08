package circuitir

import (
	"fmt"
	"regexp"
	"strconv"
	"strings"
)

var (
	numberPrefix = regexp.MustCompile(
		`(?i)^(?:(?:[0-9]+(?:\.[0-9]*)?|\.[0-9]+)(?:e[+-]?[0-9]+)?)(?:meg|mil|[tgkmunpf])?`,
	)
	namePrefix = regexp.MustCompile(`^[A-Za-z_][A-Za-z0-9_.$]*`)
	exponent   = regexp.MustCompile(`(?i)e[+-]?([0-9]+)`)
)

type expression interface {
	appendTo(*strings.Builder)
}

type atomExpression struct {
	text string
}

func (expression atomExpression) appendTo(rendered *strings.Builder) {
	rendered.WriteString(expression.text)
}

type unaryExpression struct {
	operator string
	operand  expression
}

func (expression unaryExpression) appendTo(rendered *strings.Builder) {
	rendered.WriteString(expression.operator)
	expression.operand.appendTo(rendered)
}

type binaryExpression struct {
	operator string
	left     expression
	right    expression
}

func (expression binaryExpression) appendTo(rendered *strings.Builder) {
	rendered.WriteByte('{')
	expression.left.appendTo(rendered)
	rendered.WriteByte(' ')
	rendered.WriteString(expression.operator)
	rendered.WriteByte(' ')
	expression.right.appendTo(rendered)
	rendered.WriteByte('}')
}

type expressionToken struct {
	kind   byte
	text   string
	offset int
}

const (
	tokenNumber byte = iota + 1
	tokenName
	tokenOperator
	tokenOpen
	tokenClose
	tokenEOF
)

func validateCanonicalExpression(value, location string) error {
	if err := validateString(value, location, false); err != nil {
		return err
	}
	for _, character := range value {
		if character > 0x7f {
			return validationError(location, "must use the ASCII expression subset")
		}
	}
	parsed, err := parseExpression(value)
	if err != nil {
		return validationError(location, "is invalid: %v", err)
	}
	var rendered strings.Builder
	rendered.Grow(len(value))
	parsed.appendTo(&rendered)
	if rendered.String() != value {
		return validationError(location, "is not canonical")
	}
	return nil
}

func parseExpression(value string) (expression, error) {
	stripped := strings.TrimSpace(value)
	if strings.HasPrefix(stripped, "{") && strings.HasSuffix(stripped, "}") {
		stripped = strings.TrimSpace(stripped[1 : len(stripped)-1])
	}
	tokens, err := tokenizeExpression(stripped)
	if err != nil {
		return nil, err
	}
	parser := expressionParser{tokens: tokens}
	result, err := parser.parsePrecedence(0, 0)
	if err != nil {
		return nil, err
	}
	if parser.current().kind != tokenEOF {
		return nil, fmt.Errorf(
			"unexpected token %q at offset %d", parser.current().text, parser.current().offset,
		)
	}
	return result, nil
}

func tokenizeExpression(value string) ([]expressionToken, error) {
	if value == "" {
		return nil, fmt.Errorf("empty expression")
	}
	result := make([]expressionToken, 0, len(value)/2+1)
	for index := 0; index < len(value); {
		if value[index] == ' ' || value[index] == '\t' || value[index] == '\r' || value[index] == '\n' {
			index++
			continue
		}
		if len(result) >= MaxExpressionTokens {
			return nil, fmt.Errorf("expression exceeds the %d-token limit", MaxExpressionTokens)
		}
		remaining := value[index:]
		if number := numberPrefix.FindString(remaining); number != "" {
			if err := validateNumberToken(number); err != nil {
				return nil, err
			}
			result = append(result, expressionToken{kind: tokenNumber, text: number, offset: index})
			index += len(number)
			continue
		}
		if name := namePrefix.FindString(remaining); name != "" {
			result = append(result, expressionToken{kind: tokenName, text: name, offset: index})
			index += len(name)
			continue
		}
		if strings.HasPrefix(remaining, "**") {
			result = append(result, expressionToken{kind: tokenOperator, text: "**", offset: index})
			index += 2
			continue
		}
		character := value[index]
		switch character {
		case '+', '-', '*', '/', '^':
			result = append(result, expressionToken{
				kind: tokenOperator, text: string(character), offset: index,
			})
		case '(', '{':
			result = append(result, expressionToken{kind: tokenOpen, text: "(", offset: index})
		case ')', '}':
			result = append(result, expressionToken{kind: tokenClose, text: ")", offset: index})
		default:
			return nil, fmt.Errorf("unsupported character %q at offset %d", character, index)
		}
		index++
	}
	result = append(result, expressionToken{kind: tokenEOF, offset: len(value)})
	return result, nil
}

func validateNumberToken(value string) error {
	if len(value) > MaxNumberBytes {
		return fmt.Errorf("number literal exceeds the %d-character limit", MaxNumberBytes)
	}
	match := exponent.FindStringSubmatch(value)
	if match == nil {
		return nil
	}
	digits := match[1]
	if len(digits) > 5 {
		return fmt.Errorf("number exponent magnitude exceeds 10000")
	}
	magnitude, err := strconv.Atoi(digits)
	if err != nil || magnitude > 10_000 {
		return fmt.Errorf("number exponent magnitude exceeds 10000")
	}
	return nil
}

type expressionParser struct {
	tokens []expressionToken
	index  int
	nodes  int
}

func (parser *expressionParser) accountNode() error {
	parser.nodes++
	if parser.nodes > MaxExpressionNodes {
		return fmt.Errorf("expression exceeds the %d-node limit", MaxExpressionNodes)
	}
	return nil
}

func (parser *expressionParser) current() expressionToken {
	return parser.tokens[parser.index]
}

func (parser *expressionParser) consume() expressionToken {
	token := parser.current()
	parser.index++
	return token
}

func (parser *expressionParser) parsePrecedence(minimum, depth int) (expression, error) {
	if depth > 256 {
		return nil, fmt.Errorf("expression nesting exceeds 256 levels")
	}
	token := parser.consume()
	var left expression
	switch {
	case token.kind == tokenNumber || token.kind == tokenName:
		if err := parser.accountNode(); err != nil {
			return nil, err
		}
		left = atomExpression{text: token.text}
	case token.kind == tokenOperator && (token.text == "+" || token.text == "-"):
		operand, err := parser.parsePrecedence(30, depth+1)
		if err != nil {
			return nil, err
		}
		if err := parser.accountNode(); err != nil {
			return nil, err
		}
		left = unaryExpression{operator: token.text, operand: operand}
	case token.kind == tokenOpen:
		nested, err := parser.parsePrecedence(0, depth+1)
		if err != nil {
			return nil, err
		}
		if parser.current().kind != tokenClose {
			return nil, fmt.Errorf("missing closing parenthesis")
		}
		parser.consume()
		left = nested
	default:
		return nil, fmt.Errorf("unexpected token %q at offset %d", token.text, token.offset)
	}

	precedence := map[string]int{"+": 10, "-": 10, "*": 20, "/": 20, "^": 30, "**": 30}
	for {
		level, operator := precedence[parser.current().text]
		if !operator || level < minimum {
			break
		}
		token = parser.consume()
		rightMinimum := level + 1
		if token.text == "^" || token.text == "**" {
			rightMinimum = level
		}
		right, err := parser.parsePrecedence(rightMinimum, depth+1)
		if err != nil {
			return nil, err
		}
		if err := parser.accountNode(); err != nil {
			return nil, err
		}
		left = binaryExpression{operator: token.text, left: left, right: right}
	}
	return left, nil
}
