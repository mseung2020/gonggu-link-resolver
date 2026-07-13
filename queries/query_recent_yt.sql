-- 최근 1주일 공구/공동구매 키워드 매칭 유튜브 영상 조회
-- YT_video_lists_detail(설명 있음, continuously 갱신) 기준으로 필터하고 YT_video_lists에서 통계만 보조로 join.
-- (youtube_video_info는 인덱스 없는 작은 스냅샷 테이블이라 최근 데이터 커버 못함 — 쓰지 말 것)
-- 결과는 CSV 말고 JSON으로 export할 것

SELECT d.video_id, d.title, d.publishDate, d.video_description,
       v.channel_id, v.views, v.likes, v.comments, v.subscribers, v.video_thumbnails_url, v.genre
FROM hifen.YT_video_lists_detail d
LEFT JOIN hifen.YT_video_lists v ON v.video_id = d.video_id
WHERE d.publishDate >= '2026-07-06'
  AND (d.video_description LIKE '%공구%' OR d.video_description LIKE '%공동구매%')
ORDER BY d.publishDate DESC;
