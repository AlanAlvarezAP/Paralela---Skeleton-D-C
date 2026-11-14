#ifndef HEART_BEAT_H
#define HEART_BEAT_H


#include <deque>

template <typename TYPE>
class PrivateStack
{
public:
    PrivateStack():
        data()
    {}

    void push(TYPE&& in_value)
    {
        data.push_back(std::move(in_value));
    }

    TYPE pop()
    {
        TYPE x = std::move(data.back());
        data.pop_back();

        return x;
    }

    TYPE take_oldest()
    {
        TYPE x = std::move(data.front());
        data.pop_front();

        return x;
    }

    bool can_promote() { return data.size() >= 2; }

    bool empty() { return data.empty(); }
private:
    std::deque<TYPE> data;
};




#endif