# REFLECTION CÁ NHÂN — NGUYỄN VŨ QUANG ANH

**Họ và tên:** Nguyễn Vũ Quang Anh  
**Mã học viên:** 2A202602805  
**Nhóm:** AI4L — Lớp 3A, phòng E403, cụm 2  
**Dự án:** Teach-back Studio  
**Vai trò:** Trưởng nhóm; phụ trách prompt/rubric kiến thức, backend/agent, kiểm thử và phối hợp demo

## 1. Vai trò và những công việc tôi đã thực hiện

Trong dự án Teach-back Studio, tôi đảm nhận vai trò trưởng nhóm. Trách nhiệm đầu tiên của tôi là chia công việc dựa trên thế mạnh của từng thành viên và theo dõi tiến độ qua các checkpoint. Vũ Quốc Bảo phụ trách khảo sát và thu thập bằng chứng người dùng; Mai Phan Anh Tùng tập trung vào canvas và hoàn thiện spec; còn tôi phụ trách phần kỹ thuật chính, prompt/rubric kiến thức, quy trình đánh giá và phối hợp chuẩn bị demo. Tuy có phân công riêng, cả nhóm vẫn thường xuyên đọc chéo và trao đổi để các phần khảo sát, thiết kế sản phẩm, code và bài thuyết trình không bị tách rời.

Ở phần sản phẩm, tôi tham gia xây dựng backend và agent cho luồng teach-back. Luồng chính yêu cầu người học tự giải thích kiến thức bằng lời của mình, sau đó AI đối chiếu với các điểm kiến thức K1–K4 để xác định nội dung đã hiểu, phần còn thiếu hoặc misconception. Hệ thống không đưa ngay đáp án mà ưu tiên hỏi Socratic, thu hẹp câu hỏi, đưa gợi ý có kiểm soát và chỉ chuyển sang knowledge recovery khi người học tiếp tục gặp khó khăn. Tôi cũng tham gia hoàn thiện cách xử lý câu hỏi ngoài phạm vi, bảo toàn tiến độ phiên học, hiển thị nguồn tham chiếu và chỉ cho phép hoàn thành khi người học đạt đủ điều kiện mastery và transfer.

Một đầu việc quan trọng khác của tôi là phát triển sản phẩm từ một bài thử nghiệm về hiện tượng LLM “bịa” thành hệ thống có thể hoạt động trên tám bài học. Tôi tham gia chuẩn hóa lesson catalog, kết nối luồng xử lý đa chủ đề, cập nhật giao diện, viết và chạy các bài test cho agent, lesson engine và nền tảng lưu phiên. Tôi cũng phối hợp chuẩn bị slide, demo end-to-end và tổng hợp các thay đổi cuối cùng trước khi nộp.

Về đánh giá, tôi tham gia xây dựng và chuẩn hóa golden set gồm 20 trường hợp trải trên tám bài học, bao phủ bốn lớp rủi ro: đầu vào, ngữ nghĩa, grounding và hội thoại. Nhóm đã chạy pipeline với mô hình AI thật và đạt 16/20 trường hợp, tương đương 80%; đồng thời 20/20 trường hợp không bịa nguồn. Tuy nhiên, tôi nhận thấy kết quả này mới chỉ chạm ngưỡng phần trăm, chưa đạt toàn bộ Quality Bar vì vẫn còn ba lỗi nghiêm trọng ở lớp L4_DIALOGUE, một lỗi ở lớp L3 và chưa hoàn thành bước hai người chấm tay độc lập. Việc ghi rõ hạn chế này giúp nhóm trình bày trung thực thay vì chỉ công bố một con số đẹp.

## 2. Khó khăn và cách tôi xử lý

Khó khăn lớn nhất là biến một ý tưởng giáo dục tương đối đơn giản thành một luồng AI có hành vi ổn định. Nếu agent quá dễ dãi, hệ thống có thể công nhận người học đã hiểu dù câu trả lời còn sai; ngược lại, nếu phản hồi quá cứng hoặc đưa đáp án quá sớm, sản phẩm lại trở thành chatbot giải bài thông thường và mất ý nghĩa teach-back. Tôi xử lý vấn đề này bằng cách tách rõ trạng thái tiến độ, misconception, hành động tiếp theo và bằng chứng nguồn; đồng thời thiết kế thang hỗ trợ theo từng knowledge gap thay vì dùng một câu trả lời chung cho mọi trường hợp.

Khó khăn thứ hai là quản lý phạm vi trong thời gian hackathon ngắn. Nhóm từng có nhiều phiên bản golden set và một số luồng kỹ thuật khác nhau, gây nguy cơ nhầm lẫn giữa kết quả luật offline và năng lực thật của mô hình. Tôi đã cùng nhóm hợp nhất bộ đánh giá, yêu cầu runner đánh giá chính thức chỉ chạy online với OpenAI, lưu artifact của từng lần chạy và cập nhật spec theo đúng kết quả cuối. Qua việc này, tôi hiểu rằng làm sản phẩm AI không chỉ là khiến demo chạy được mà còn phải tạo ra một quy trình có thể kiểm tra, lặp lại và giải thích.

Ở vai trò trưởng nhóm, tôi cũng nhận ra việc “chia việc” chưa đủ. Tôi cần bảo đảm mọi người hiểu mục tiêu chung, biết phần của mình ảnh hưởng đến phần khác như thế nào và có đủ thời gian để kiểm tra chéo. Có thời điểm khối lượng kỹ thuật tập trung nhiều vào một người, khiến việc truyền đạt và đồng bộ kiến thức trong nhóm chưa tốt như mong muốn. Nếu làm lại, tôi sẽ chốt sớm hơn tiêu chí hoàn thành của từng đầu việc, tổ chức các mốc review ngắn và yêu cầu mỗi thành viên trình bày lại phần của người khác trước buổi demo.

## 3. Những điều tôi thu hoạch được

Bài học lớn nhất của tôi là phải bắt đầu từ vấn đề thật của người dùng thay vì bắt đầu từ công nghệ. Kết quả khảo sát cho thấy 17/20 người từng cảm thấy hiểu khi xem bài nhưng không thể giải thích lại, và 20/20 người sẵn sàng thử hình thức dạy lại cho AI. Những con số này giúp nhóm chọn đúng lát cắt: không xây thêm một chatbot hỏi đáp chung chung mà tạo một bước kiểm tra hiểu bài chủ động ngay sau khi học.

Tôi cũng học được cách xây dựng tiêu chí chất lượng cụ thể cho sản phẩm AI. Một tỷ lệ pass chung chưa đủ để khẳng định hệ thống tốt; cần phân loại lỗi theo mức độ nghiêm trọng, kiểm tra nguồn trích dẫn, điều kiện dừng, hiện tượng lộ đáp án và khả năng sửa misconception qua nhiều lượt hội thoại. Kết quả 80% của nhóm cho thấy prototype đã có nền tảng nhưng ba lỗi L4 vẫn quan trọng hơn nhiều trường hợp đúng thông thường. Điều này giúp tôi thay đổi cách nhìn: đánh giá AI phải dựa trên rủi ro và cost of error, không chỉ dựa vào accuracy trung bình.

Qua quá trình code, tôi hiểu rõ hơn về việc quản lý trạng thái hội thoại nhiều lượt, thiết kế agent theo luồng, kết nối lesson catalog với rubric và duy trì tính nhất quán giữa backend, UI và bộ eval. Qua validation, tôi học được rằng phản hồi người dùng nên được thu thập bằng cách giao một nhiệm vụ cụ thể rồi quan sát, thay vì chỉ hỏi họ có thích sản phẩm hay không. Những chi tiết nhỏ như người dùng chưa hiểu suggestion chip chỉ điền nội dung vào ô chat, hoặc cần thông báo rõ hơn khi câu hỏi ngoài phạm vi, có thể ảnh hưởng trực tiếp đến khả năng sử dụng dù logic AI phía sau hoạt động đúng.

Cuối cùng, dự án giúp tôi rèn luyện khả năng lãnh đạo trong điều kiện thời gian ngắn: ưu tiên việc quan trọng, phối hợp các thành viên, đưa ra quyết định khi dữ liệu chưa hoàn hảo và chịu trách nhiệm về kết quả chung. Tôi thấy mình đã đóng góp tốt ở phần kỹ thuật, kiểm thử và tích hợp sản phẩm, nhưng cần cải thiện thêm ở việc ủy quyền, tài liệu hóa sớm và tạo nhiều cơ hội để các thành viên cùng nắm phần cốt lõi.

## 4. Nếu tiếp tục phát triển dự án

Nếu có thêm thời gian, tôi sẽ ưu tiên sửa ba lỗi L4 trong golden set trước, sau đó hoàn tất đánh giá thủ công bởi hai người để xác nhận Quality Bar. Tiếp theo, tôi sẽ cải thiện trải nghiệm suggestion chip và trạng thái `out_of_scope`, làm nổi bật nguồn phù hợp nhất với knowledge gap, đồng thời cá nhân hóa mức gợi ý theo năng lực và số lần người học trả lời sai. Tôi cũng muốn mở rộng validation với nhiều người học hơn, so sánh kết quả teach-back với quiz thông thường và đo xem người học có ghi nhớ tốt hơn sau một khoảng thời gian hay không.

Nhìn chung, Teach-back Studio giúp tôi hiểu trọn vẹn hơn chu trình xây dựng một sản phẩm AI: tìm vấn đề thật, chọn lát cắt nhỏ, xây prototype, đặt chuẩn đánh giá, kiểm thử rủi ro, quan sát người dùng và trung thực với giới hạn của kết quả. Đây là phần thu hoạch có giá trị nhất đối với tôi sau dự án.
