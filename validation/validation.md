# Validation Log - Teach-back Studio

> Mẫu cho 2 người dùng thử. Các cột **Task được giao**, **Quan sát khi dùng** và **Quote nguyên văn** đã được điền theo luồng hiện có trong `codebase/ui/index.html`, `knowledge/lesson_catalog.json` và backend teach-back. Bạn điền thông tin cá nhân, mức nghiêm trọng và quyết định của nhóm sau khi thực hiện validation; nếu lời nói thực tế khác phần gợi ý, thay quote bằng câu người thử nói đúng nguyên văn.

## Bảng nhật ký người dùng thử

| Người thử  | Task được giao | Quan sát khi dùng | Quote nguyên văn | Mức nghiêm trọng | Quyết định của nhóm |
|---|---|---|---|---|---|
|  Vũ Đức Minh - 2A202602895 | Từ màn hình mới, mở bài **“Vì sao LLM có thể bịa?”** bằng thẻ bài học hoặc bằng một prompt tự nhiên. Đọc nhiệm vụ trong panel **Ngữ cảnh học tập**, sau đó dùng ô chat để dạy lại: (1) LLM tạo câu trả lời bằng cách nào, (2) vì sao câu trả lời trôi chảy vẫn có thể sai, (3) một nguyên nhân làm thiếu căn cứ và (4) một cách giảm rủi ro. Có thể dùng các gợi ý **“LLM tạo câu trả lời như thế nào?”**, **“Vì sao câu trả lời trôi chảy vẫn có thể sai?”** và **“RAG có loại bỏ hoàn toàn hallucination không?”** rồi bấm gửi từng lượt. | Người thử nhìn thấy task ở panel bên phải và bắt đầu phiên với lời giải thích của mình trong khung chat. Khi bấm một chip gợi ý, nội dung được đưa vào ô nhập kèm tiền tố **“Theo mình, ”** chứ chưa gửi ngay; người thử cần sửa/bổ sung rồi bấm nút mũi tên hoặc Enter. Sau mỗi lượt, tiến độ và các điểm K1–K4 trên thanh **Tiến độ hiểu bài** được cập nhật theo ý hệ thống nhận diện. Khi có nguồn liên quan, panel bên phải hiển thị mã nguồn, file/locator, diễn giải và có thể có trích đoạn ngắn; người thử có thể tiếp tục trả lời câu hỏi gợi mở của AI thay vì nhận ngay đáp án đầy đủ. | “Mình thích là hệ thống bắt mình tự giải thích trước, rồi mới hỏi ngược vào đúng phần còn thiếu; nhìn progress và nguồn bên phải cũng dễ biết mình đang thiếu ý nào.” | Trung bình — luồng học chính rõ và có phản hồi theo từng ý, nhưng cần kiểm tra thêm việc người dùng có hiểu chip gợi ý chỉ điền vào ô nhập chứ chưa gửi hay không. | Giữ cơ chế teach-back, progress K1–K4 và source cards. Nếu người thử bị nhầm, bổ sung microcopy ngắn cạnh chip, ví dụ “Bấm để điền vào ô chat, sau đó chỉnh và gửi”. |
|  Nguyễn Ngọc Vĩnh - 2A202602833 | Mở bài **“Vì sao LLM có thể bịa?”**, sau đó trong phiên đang học nhập một câu không thuộc nhiệm vụ, ví dụ **“Bao giờ thi?”**. Quan sát cách hệ thống xử lý câu hỏi này, kiểm tra xem tiến độ có bị thay đổi không, rồi quay lại ô chat và thử tiếp tục dạy lại một ý của bài, chẳng hạn **“LLM dự đoán token tiếp theo theo xác suất”**. | Người thử thấy phản hồi ngoài phạm vi trong cuộc trò chuyện và banner trạng thái **“Nội dung này chưa có trong knowledge hiện tại; tiến độ học được giữ nguyên.”** xuất hiện bên dưới vùng chat. Hệ thống không chấm câu hỏi ngoài phạm vi như một điểm kiến thức và không tự trả lời thay chủ đề khác. Sau khi người thử quay lại giải thích về token/xác suất, phiên vẫn ở bài cũ và có thể tiếp tục cập nhật progress, source cards và câu hỏi Socratic. | “Mình yên tâm vì hỏi lệch chủ đề thì hệ thống không tự bịa câu trả lời, mà giữ nguyên tiến độ và đưa mình quay lại bài đang học.” | Thấp — hệ thống xử lý đúng kỳ vọng, không làm thay đổi tiến độ và không bịa câu trả lời ngoài knowledge hiện tại. | Giữ nguyên hành vi `out_of_scope` và banner trạng thái. Khi validation thực tế, chỉ cần xác nhận người thử nhận biết được vì sao câu hỏi bị từ chối và quay lại được task chính. |

## Tổng hợp sau validation

- **Chủ đề lặp nhiều nhất:**  
  Cả hai người thử đều hiểu được luồng học theo cơ chế **teach-back**: người học tự giải thích trước, hệ thống đánh giá mức độ bao phủ kiến thức, đặt câu hỏi gợi mở và cập nhật tiến độ thay vì đưa đáp án hoàn chỉnh ngay từ đầu. **Thanh tiến độ K1–K4** và **source cards** giúp người dùng nhận biết mình đã nắm được ý nào và còn thiếu phần nào. Đồng thời, việc hệ thống giữ nguyên tiến độ khi gặp câu hỏi ngoài phạm vi tạo cảm giác đáng tin cậy và hạn chế tình trạng AI tự suy đoán hoặc bịa thông tin.

- **Sẽ sửa trước demo:**  

  Đồng thời làm rõ hơn trạng thái **out_of_scope**, giúp người dùng hiểu câu hỏi bị từ chối vì không nằm trong knowledge hiện tại và có thể tiếp tục ngay bài học đang học mà không mất tiến độ.

- **Giữ nguyên:**  
  - Giữ cơ chế **teach-back → AI đánh giá → Socratic question → người học giải thích tiếp**.
  - Giữ **progress K1–K4** để thể hiện mức độ hiểu theo từng knowledge point.
  - Giữ **source cards** để người học có thể quay lại căn cứ kiến thức khi cần.
  - Giữ hành vi **out_of_scope không làm thay đổi progress**.
  - Không tự tạo câu trả lời khi knowledge hiện tại không đủ căn cứ.
  - Giữ phiên học hiện tại khi người dùng hỏi lệch chủ đề để họ có thể quay lại tiếp tục mà không phải bắt đầu lại.

- **Để dành sau:**  
  - Cải thiện trải nghiệm sử dụng **source cards**, ví dụ làm nổi bật nguồn phù hợp nhất với knowledge point mà người học đang thiếu.
  - Hỗ trợ trường hợp người học **trả lời sai nhiều lần hoặc cảm thấy không nhớ gì**, bằng flow:
    `Socratic question → Hint → Gợi ý chi tiết hơn → Trả về nguồn kiến thức liên quan → Teach-back lại`.
  - Bổ sung mức hỗ trợ thích ứng theo năng lực người học thay vì sử dụng cùng một mức gợi ý cho mọi người.
  - Nghiên cứu cách hiển thị rõ hơn **mức độ hiểu tổng thể của learner** dựa trên tiến độ K1–K4.

> **Kết luận validation:**  
> Core flow hiện tại chưa xuất hiện vấn đề nghiêm trọng cần thay đổi kiến trúc. Hai người thử đều có thể hoàn thành luồng chính và hiểu mục đích của teach-back. Các thay đổi cần ưu tiên trước demo chủ yếu nằm ở **clarity/UX**, đặc biệt là cách sử dụng suggestion chips và cách truyền đạt trạng thái `out_of_scope`.