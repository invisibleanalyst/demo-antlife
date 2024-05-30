"use client";
import React from "react";
import Image from "next/image";
import ChatLoader from "components/ChatLoader/page";
import panda from "public/svg/panda.svg";
import { ChatScreenProps } from "./chat-types";
import ChatInput from "./ChatInput";
import UserChatBubble from "./UserChatBubble";
import AIChatBubble from "./AIChatBubble";
import { ChatPlaceholderLoader } from "components/Skeletons";
import StartChatIcon from "components/Icons/StartChatIcon";

const ChatScreen = ({
  scrollLoad,
  chatData,
  setChatData,
  isTyping,
  queryRef,
  sendQuery,
  setSendQuery,
  hasMore,
  setFollowUpQuestionDiv,
  followUpQuestionDiv,
  followUpQuestions,
  loading,
  scrollDivRef,
}: ChatScreenProps) => {
  const handleNewMessage = () => {
    setSendQuery(true);
  };

  return (
    <div className="flex antialiased text-gray-800 w-full">
      <div className="flex flex-col flex-auto flex-shrink-0 rounded-2xl h-full dark:!bg-[#000] w-[calc(100vw-22px)] md:w-full">
        <div
          className="flex flex-col h-full overflow-x-scroll mb-4 custom-scroll"
          id="chat-div"
          ref={scrollDivRef}
        >
          <div className="mx-auto w-full max-w-[900px]">
            <div
              className="flex flex-col flex-auto px-2 md:px-4"
              style={{ marginTop: "1rem" }}
            >
              <div
                className="flex flex-col flex-auto flex-shrink-0 gap-4 rounded-2xl h-full md:p-4 dark:!bg-[#000]"
                id="scrollableDiv"
              >
                {scrollLoad ? (
                  <ChatPlaceholderLoader />
                ) : (
                  <>
                    {hasMore && (
                      <div className="flex justify-center items-center">
                        <ChatPlaceholderLoader />
                      </div>
                    )}
                    {loading ? (
                      <ChatPlaceholderLoader />
                    ) : (
                      <>
                        {chatData?.length === 0 && (
                          <div className="flex items-center justify-center w-full h-full m-auto dark:text-white text-lg font-montserrat">
                            How can I help you today?
                          </div>
                        )}
                        {chatData?.map((chat, chatIndex) => (
                          <div key={chat.id}>
                            {chat?.query ? (
                              <UserChatBubble query={chat?.query} />
                            ) : (
                              <AIChatBubble
                                chatData={chatData}
                                setChatData={setChatData}
                                key={chat.id}
                                chat={chat}
                                lastIndex={chatData?.length - 1 === chatIndex}
                              />
                            )}
                          </div>
                        ))}
                        {isTyping && (
                          <>
                            <div className="col-start-1 col-end-8 p-3 rounded-lg">
                              <div className="flex flex-row items-center gap-2">
                                <div className="flex justify-center h-10 rounded-full bg-[#191919] text-white flex-shrink-0">
                                  <Image
                                    src={panda}
                                    alt="logo"
                                    className="h-7 w-7 md:h-10 md:w-10 rounded-full"
                                  />
                                </div>
                                <div className="relative mr-3 text-sm py-2 bg-white shadow rounded-xl">
                                  <div>
                                    <ChatLoader />
                                  </div>
                                </div>
                              </div>
                            </div>
                          </>
                        )}
                      </>
                    )}
                  </>
                )}
              </div>
            </div>
          </div>
        </div>
        <div className="mx-auto w-full max-w-[900px]">
          {followUpQuestionDiv && (
            <div className="flex flex-col flex-wrap items-start px-2 md:px-6 gap-2 pb-2">
              {followUpQuestions?.map((item) => (
                <div
                  key={item.id}
                  className="py-2 px-4 flex items-center bg-blue-700 rounded-md cursor-pointer text-[15px]"
                  onClick={() => {
                    setSendQuery(true);
                    setFollowUpQuestionDiv(false);
                    queryRef.current.value = item.question;
                  }}
                >
                  <StartChatIcon />
                  <div className="text-white ml-2">{item.question}</div>
                </div>
              ))}
            </div>
          )}
          <ChatInput
            onNewMessage={handleNewMessage}
            queryRef={queryRef}
            sendQuery={sendQuery}
            extra="px-2 md:px-6 pb-4"
          />
        </div>
      </div>
    </div>
  );
};

export default ChatScreen;
