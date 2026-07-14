-- 최근 1주일 공구/공동구매 키워드 매칭 인스타그램 포스트 조회
-- 날짜는 오늘 기준으로 바꿔서 쓰면 됨 (CURDATE()-INTERVAL 7 DAY 대신 리터럴로 고정해 DB 서버 시계와 무관하게 재현 가능하게 함)
-- 결과는 CSV 말고 JSON으로 export할 것 (caption에 쉼표/줄바꿈 있어서 CSV 컬럼이 밀림)

SELECT p.post_id, p.user_id, p.url, p.publish_date, p.is_reel, p.thumbnail, p.likes, p.comments, p.views,
       d.description
FROM hifen.instagram_post p
JOIN hifen.instagram_post_description d ON d.post_id = p.post_id
WHERE p.publish_date >= '2026-07-07'
  AND (d.description LIKE '%공구%' OR d.description LIKE '%공동구매%')
ORDER BY p.publish_date DESC;
