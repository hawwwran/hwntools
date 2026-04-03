#!/bin/bash
# @package: hawwwran.random
# @version: 1.0.0
# @label: Password Generator
# @description: Generate secure random passwords with configurable length and character sets
# @icon: dialog-password

GREEN='\033[0;32m'
WHITE='\033[0;37m'
CYAN='\033[0;36m'
YELLOW='\033[1;33m'
NC='\033[0m'

echo -e "${CYAN}Password Generator${NC}\n"

# Password length
read -rp "$(echo -e "${WHITE}Length [${GREEN}16${WHITE}]: ${NC}")" LENGTH
LENGTH="${LENGTH:-16}"

if ! [[ "$LENGTH" =~ ^[0-9]+$ ]] || [ "$LENGTH" -lt 4 ] || [ "$LENGTH" -gt 128 ]; then
    echo -e "\n${YELLOW}Length must be a number between 4 and 128.${NC}"
    exit 1
fi

# Character set options
echo -e "\n${WHITE}Include:${NC}"
read -rp "$(echo -e "  ${WHITE}Uppercase (A-Z)?    [${GREEN}Y${WHITE}/n]: ${NC}")" INC_UPPER
read -rp "$(echo -e "  ${WHITE}Lowercase (a-z)?    [${GREEN}Y${WHITE}/n]: ${NC}")" INC_LOWER
read -rp "$(echo -e "  ${WHITE}Digits (0-9)?       [${GREEN}Y${WHITE}/n]: ${NC}")" INC_DIGITS
read -rp "$(echo -e "  ${WHITE}Symbols (!@#..)?    [${GREEN}Y${WHITE}/n]: ${NC}")" INC_SYMBOLS

CHARS=""
[[ "${INC_UPPER,,}" != "n" ]]   && CHARS="${CHARS}ABCDEFGHIJKLMNOPQRSTUVWXYZ"
[[ "${INC_LOWER,,}" != "n" ]]   && CHARS="${CHARS}abcdefghijklmnopqrstuvwxyz"
[[ "${INC_DIGITS,,}" != "n" ]]  && CHARS="${CHARS}0123456789"
[[ "${INC_SYMBOLS,,}" != "n" ]] && CHARS="${CHARS}!@#\$%^&*()-_=+[]{}|;:,.<>?"

if [ -z "$CHARS" ]; then
    echo -e "\n${YELLOW}No character sets selected.${NC}"
    exit 1
fi

# How many passwords
read -rp "$(echo -e "\n${WHITE}How many? [${GREEN}5${WHITE}]: ${NC}")" COUNT
COUNT="${COUNT:-5}"

if ! [[ "$COUNT" =~ ^[0-9]+$ ]] || [ "$COUNT" -lt 1 ] || [ "$COUNT" -gt 50 ]; then
    echo -e "\n${YELLOW}Count must be a number between 1 and 50.${NC}"
    exit 1
fi

echo -e "\n${CYAN}Generated passwords:${NC}\n"

for i in $(seq 1 "$COUNT"); do
    PASS=""
    for _ in $(seq 1 "$LENGTH"); do
        RAND=$((RANDOM % ${#CHARS}))
        PASS="${PASS}${CHARS:$RAND:1}"
    done
    printf "  ${GREEN}%2d${WHITE})  ${YELLOW}%s${NC}\n" "$i" "$PASS"
done

echo ""
