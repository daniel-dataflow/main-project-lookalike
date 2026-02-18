#!/bin/bash
# Git 히스토리에서 config.py의 민감한 정보만 제거하는 스크립트

# 민감한 문자열 목록
cat > /tmp/passwords.txt << 'EOF'
DataPass2024!
lookalike-session-secret-change-in-production-2024
admin1234!
EOF

# git filter-repo로 민감한 문자열을 안전한 값으로 치환
git filter-repo --replace-text /tmp/passwords.txt --force

# 임시 파일 삭제
rm /tmp/passwords.txt

echo "✅ Git 히스토리 정리 완료"
echo "⚠️  다음 명령어로 강제 푸시 필요:"
echo "   git push origin --force --all"
