# Báo cáo đóng góp cá nhân — Mai Phan Anh Tùng (2A202602980)

## 1. Vai trò cá nhân

Trong dự án **Teach-back Studio**, tôi phụ trách phần kiến trúc hội thoại của AI tutor và cơ chế đánh giá chất lượng. Vai trò chính của tôi là chuyển luồng xử lý từ cách viết tuần tự sang đồ thị trạng thái LangGraph, để hệ thống có thể quản lý một phiên học nhiều lượt, lưu được tiến độ và đưa ra câu hỏi gợi mở an toàn hơn.

## 2. Phần việc trực tiếp phụ trách

- Thiết kế và hiện thực `agent_graph.py`: mô hình hóa luồng một lượt học thành các node rõ ràng như đánh giá phần giải thích, kiểm tra căn cứ, cập nhật tiến độ/hiểu sai, truy xuất nguồn, soạn câu hỏi và lưu trạng thái.
- Tích hợp LangGraph cùng SQLite checkpoint vào backend. Nhờ đó, phiên học không mất toàn bộ trạng thái khi server khởi động lại; các dữ liệu như knowledge point đã đạt, misconception, số lần thử và trạng thái transfer được duy trì qua nhiều lượt HTTP.
- Chỉnh sửa `agent_core.py` và `server.py` để chuyển logic nghiệp vụ sang graph, đồng thời vẫn giữ tương thích với API hiện có.
- Rà soát và sửa các lỗi nhận diện bằng regex tiếng Việt trong knowledge point và misconception, ví dụ lỗi khớp nhầm từ không dấu hoặc khớp từ con trong một từ khác.
- Tách bước sinh câu hỏi Socratic khỏi bước đánh giá; bổ sung cơ chế kiểm tra an toàn, thử sinh lại một lần nếu câu hỏi chưa phù hợp, sau đó mới dùng câu hỏi dự phòng.
- Bổ sung giới hạn lượt hội thoại, reset checkpoint thật và tài liệu `MIGRATION_LANGGRAPH.md`; xây dựng/hoàn thiện phần đặc tả và tập kiểm thử rủi ro (golden set) để kiểm tra các tình huống đa lượt, prompt injection, ngoài phạm vi và hiểu sai kiến thức.
- Mở rộng lesson pipeline, dữ liệu bài học và các unit test liên quan để hệ thống vận hành theo nhiều bài học thay vì chỉ một ngữ cảnh minh họa.

## 3. Cách tôi ứng dụng AI trong quá trình xây dựng

AI không chỉ là giao diện chat mà là thành phần ra quyết định trong sản phẩm. Khi người học diễn giải kiến thức tự do, model đánh giá câu trả lời theo rubric của bài học để xác định: ý nào đã được bao phủ, ý nào còn thiếu, có misconception hay không, và hành động kế tiếp nên là gì. Đầu ra của AI được ràng buộc bằng nguồn kiến thức đã chọn; hệ thống chỉ hiển thị source card/citation thuộc đúng bài học thay vì để model trả lời tự do.

Tôi cũng dùng AI để soạn câu hỏi Socratic theo đúng knowledge gap hoặc misconception vừa phát hiện. Tuy nhiên, phần sinh câu hỏi không được tin cậy tuyệt đối: graph kiểm tra câu hỏi trước khi trả về, cho model thử lại khi câu hỏi lạc đề hoặc thiếu an toàn, và có câu hỏi dự phòng xác định sẵn. Cách kết hợp này giúp tận dụng khả năng hiểu ngôn ngữ tự nhiên của AI nhưng vẫn giữ các ràng buộc kiểm soát được bằng code, rubric và dữ liệu nguồn.

Trong giai đoạn đánh giá, nhóm chạy golden set bằng model thật thay vì chỉ dựa vào demo. Các ca kiểm thử gồm câu trả lời mơ hồ, câu hỏi ngoài phạm vi, yêu cầu đổi vai/prompt injection, misconception và trường hợp người học đã sửa được hiểu sai. Kết quả được ghi log để đối chiếu output AI với expected behavior, từ đó tìm lỗi cụ thể thay vì đánh giá cảm tính.

## 4. Bài học thực tế rút ra từ thất bại của nhóm

Một thất bại đáng chú ý xảy ra ngay khi chuyển sang LangGraph: node cuối chỉ trả kết quả trong một trường lồng nhau mà không ghi các trường bền như `covered_points`, `unresolved_misconceptions` và `attempts_by_gap` về state cấp cao. Vì vậy, khi đi qua HTTP nhiều lượt, checkpoint SQLite của lượt sau không đọc được tiến độ đã có; người học vừa trả lời đúng ở lượt trước vẫn có thể bị xem như bắt đầu lại. Đường gọi trực tiếp trong code vẫn chạy nên lỗi này dễ bị che khuất.

Bài học tôi rút ra là: với hệ thống AI đa lượt, không thể chỉ kiểm tra chất lượng của một câu trả lời hoặc unit test một hàm riêng lẻ. Cần kiểm thử theo đúng đường đi người dùng thực tế — HTTP → persistence → restart → lượt tiếp theo — và xác định rõ state nào là dữ liệu bền bắt buộc. Sau khi phát hiện, tôi sửa node `finalize_and_persist` để trả về đầy đủ state ở cấp cao và kiểm tra lại kịch bản multi-turn, restart server và reset phiên.

Một bài học bổ sung từ golden set là tỷ lệ pass tự động 16/20 chưa đồng nghĩa hệ thống đủ an toàn. Ba ca lỗi còn lại thuộc lớp hội thoại: AI bỏ sót misconception, không sửa một khẳng định sai, hoặc vẫn giữ misconception dù người học đã sửa đúng. Điều này cho thấy đánh giá AI cần ưu tiên các lỗi có hậu quả lớn như dạy sai hoặc “nghi ngờ oan”, thay vì chỉ nhìn vào tỷ lệ tổng. Vì vậy, nhóm dùng các ca L1/L4 như điều kiện chặn chất lượng và tiếp tục ưu tiên sửa chúng trước khi coi sản phẩm đạt yêu cầu.
